"""Sunrise and sunset from Home Assistant's own ``sun.sun`` entity.

The day/night shading (graph-spec §4.9) needs every sunrise and sunset in the
60 h window. HA already computes them for the configured home, so nothing here
does astronomy: the app reads ``sun.sun`` through the Supervisor's Core API
proxy (``homeassistant_api: true`` in ``config.yaml``) and extrapolates.

``sun.sun`` only carries the *next* rising and the *next* setting. One day on,
either moves by a few minutes at most, so ``next_rising + k * 24 h`` is good
enough for a shade whose edge is a 2 h ramp. Stepping back from the next
events gives the past ones, so a cached reading still covers the window after
its own events have passed.

Polar day and polar night are the exception: there the next event is more
than a day away, a 24 h step would invent sunrises that never happen, so only
the two real events and the entity's current state are used.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

STATE_URL = os.environ.get("WG_SUN_URL", "http://supervisor/core/api/states/sun.sun")
CONTAINER_ENV = "/run/s6/container_environment"
REFRESH_SECONDS = 600
TIMEOUT_SECONDS = 5
DAY = 86400
EXTRAPOLATE_DAYS = 4  # the window is 60 h; 4 days either way is ample


def supervisor_token() -> str:
    """``SUPERVISOR_TOKEN``, from the environment or s6's copy of it.

    The CMD is started by s6, which does not hand it the container environment
    (localzone.py found the same with ``TZ``); s6 keeps one file per variable.
    """
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if token:
        return token
    try:
        return Path(CONTAINER_ENV, "SUPERVISOR_TOKEN").read_text().strip()
    except OSError:
        return ""


def _epoch(value) -> int | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def transitions(sun: dict | None, start: float, end: float) -> tuple[bool, list[tuple[int, bool]]] | None:
    """``(day_at_start, [(epoch, is_day_after), ...])`` for ``start..end``.

    ``sun`` is the ``sun.sun`` reading: ``state`` plus ``next_rising`` and
    ``next_setting`` as ISO times. ``None`` when it cannot say anything, which
    draws no shading at all.
    """
    if not sun:
        return None
    rising, setting = _epoch(sun.get("next_rising")), _epoch(sun.get("next_setting"))
    if rising is None or setting is None:
        return None

    if min(rising, setting) - start > DAY:
        # Polar: the two real events only, the state in between from HA.
        events = sorted([(rising, True), (setting, False)])
        day = str(sun.get("state")) == "above_horizon"
        return day, [event for event in events if start < event[0] < end]

    events = sorted(
        [(rising + k * DAY, True) for k in range(-EXTRAPOLATE_DAYS, EXTRAPOLATE_DAYS + 1)]
        + [(setting + k * DAY, False) for k in range(-EXTRAPOLATE_DAYS, EXTRAPOLATE_DAYS + 1)]
    )
    before = [is_day for moment, is_day in events if moment <= start]
    if not before:
        return None  # a reading older than the extrapolation reaches
    return before[-1], [event for event in events if start < event[0] < end]


def fixture(now: float, rise_hour: int = 7, set_hour: int = 19) -> dict:
    """A ``sun.sun`` reading for local renders: sunrise 07:00, sunset 19:00."""
    local = datetime.fromtimestamp(now).astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)

    def upcoming(hour: int) -> str:
        moment = midnight.replace(hour=hour)
        if moment.timestamp() <= now:
            moment = datetime.fromtimestamp(moment.timestamp() + DAY).astimezone()
        return moment.isoformat()

    is_day = rise_hour <= local.hour < set_hour
    return {
        "state": "above_horizon" if is_day else "below_horizon",
        "next_rising": upcoming(rise_hour),
        "next_setting": upcoming(set_hour),
    }


class SunReader:
    """The last good ``sun.sun`` reading, refreshed every 10 minutes."""

    def __init__(self, url: str = STATE_URL, token: str | None = None) -> None:
        self.url = url
        self.token = token if token is not None else supervisor_token()
        self._lock = threading.Lock()
        self._reading: dict | None = None
        self._fetched_at: float | None = None
        self.last_error: str | None = None

    def fetch_once(self) -> bool:
        request = urllib.request.Request(self.url, headers={"Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                document = json.load(response)
            attributes = document.get("attributes") or {}
            reading = {
                "state": document.get("state"),
                "next_rising": attributes.get("next_rising"),
                "next_setting": attributes.get("next_setting"),
            }
            if transitions(reading, time.time(), time.time()) is None:
                raise ValueError("sun.sun has no next_rising/next_setting")
        except urllib.error.HTTPError as exc:
            hint = " (needs homeassistant_api: true)" if exc.code in (401, 403) else ""
            return self._fail(f"HTTP {exc.code}{hint}")
        except (OSError, ValueError, AttributeError) as exc:
            return self._fail(str(exc) or type(exc).__name__)
        with self._lock:
            self._reading, self._fetched_at, self.last_error = reading, time.time(), None
        return True

    def _fail(self, message: str) -> bool:
        with self._lock:
            if self.last_error != message:
                print(f"wettergraph: sun.sun unreadable, no day/night shading: {message}", flush=True)
            self.last_error = message
        return False

    def reading(self) -> dict | None:
        with self._lock:
            return dict(self._reading) if self._reading else None

    def status(self) -> dict:
        with self._lock:
            return {
                "reading": dict(self._reading) if self._reading else None,
                "age_seconds": None if self._fetched_at is None else time.time() - self._fetched_at,
                "last_error": self.last_error,
            }

    def run_forever(self, stop: threading.Event) -> None:
        """Refresh every 10 minutes; the first read is the caller's, at startup."""
        while not stop.wait(REFRESH_SECONDS):
            self.fetch_once()
