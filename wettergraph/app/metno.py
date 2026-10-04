#!/usr/bin/env python3
"""met.no locationforecast client and last-good cache for the Wettergraph app.

**Server-side only.** met.no forbids browser-side requests to the
``locationforecast`` API, CORS header notwithstanding ("it is not possible to
add your own User-Agent header... Do not use this in production
environments"). The app is the only client; the dashboard never calls met.no.

Contract (ticket 04):

* ``locationforecast/2.0/compact`` is open. No API key.
* Every request carries a descriptive ``User-Agent``; met.no answers ``403``
  without one.
* Polling respects the response's ``Expires``. Once it has passed, the next
  request carries ``If-Modified-Since``; ``304`` is a success, not a failure.
* The last good series is cached under ``/data`` and survives a restart.
* A failed fetch degrades to the cached series. The renderer keeps drawing it
  and marks it stale after ``STALE_AFTER_SECONDS`` (graph-spec §8.1).
* ``403`` and ``429`` are named and logged as such, and backed off. The poller
  never raises out of its loop and never crash-loops.

Normalised sample shape - what the renderer consumes::

    {"time": "2026-09-22T18:00:00Z",     # ISO-8601 UTC, exactly as met.no sent it
     "temperature": 12.3,                # °C, no conversion anywhere (spec §5.7)
     "precipitation": 0.0,               # mm/h: the next hour's amount
     "symbol_code": "partlycloudy_day"}  # met.no code; day/night comes from the suffix

A sample whose entry carries no precipitation hook gets ``precipitation: None``
and the renderer treats it as dry. An entry with no temperature is skipped: it
has no place on the curve.

CLI (local checks, not used in the container)::

    python3 wettergraph/app/metno.py --cache-dir /tmp/wg --once
    python3 wettergraph/app/metno.py --cache-dir /tmp/wg --watch
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

FORECAST_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
CACHE_FILE = "forecast-cache.json"
CACHE_SCHEMA = 1

STALE_AFTER_SECONDS = 6 * 3600  # graph-spec §8.1
FALLBACK_POLL_SECONDS = 30 * 60  # when the response carries no Expires
MIN_SLEEP_SECONDS = 30
MAX_SLEEP_SECONDS = 3600
FAILURE_BACKOFF_START = 60
FAILURE_BACKOFF_MAX = 30 * 60

PLACE_ID_DEFAULT = "2-6325496"
LAT_DEFAULT = 48.1746
LON_DEFAULT = 11.5538

DEFAULT_CACHE_DIR = Path(os.environ.get("WG_DATA", "/data"))

APP_VERSION = os.environ.get("BUILD_VERSION") or "0.6.0"
# met.no requires a User-Agent that names the application and gives a contact;
# without one it answers 403. The repo URL is the contact.
USER_AGENT = (
    f"Wettergraph/{APP_VERSION} "
    "(Home Assistant app; +https://github.com/macmacs/ha-wettergraph)"
)


def _log(message: str) -> None:
    print(message, flush=True)


class ForecastFetchError(RuntimeError):
    """A fetch that produced no data and is not a named met.no failure."""


class MetnoForbidden(ForecastFetchError):
    """met.no answered 403: the request was rejected, usually the User-Agent."""


class MetnoRateLimited(ForecastFetchError):
    """met.no answered 429; ``retry_after`` holds the header value if sent."""

    def __init__(self, message: str, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _epoch(value: str | None) -> float | None:
    """HTTP date header -> epoch seconds, or None when absent/unparsable."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _hook(data: dict, name: str) -> tuple[float | None, str | None]:
    hook = data.get(name) or {}
    details = hook.get("details") or {}
    summary = hook.get("summary") or {}
    amount = details.get("precipitation_amount")
    return amount, summary.get("symbol_code")


def _precipitation_and_symbol(data: dict) -> tuple[float | None, str | None]:
    """mm/h and symbol code from the finest hook the entry carries.

    ``next_1_hours`` is already an hourly amount. ``next_6_hours`` and
    ``next_12_hours`` are totals over their span, divided back to mm/h so the
    whole series stays hourly.
    """
    for name, hours in (("next_1_hours", 1), ("next_6_hours", 6), ("next_12_hours", 12)):
        amount, symbol = _hook(data, name)
        if amount is not None or symbol is not None:
            return (amount / hours if amount is not None else None), symbol
    return None, None


def normalise(payload: dict) -> list[dict]:
    """met.no ``compact`` payload -> the documented sample list."""
    try:
        timeseries = payload["properties"]["timeseries"]
    except (KeyError, TypeError) as exc:
        raise ForecastFetchError(f"unexpected payload shape: {exc!r}") from exc
    if not isinstance(timeseries, list):
        raise ForecastFetchError("unexpected payload shape: timeseries is not a list")

    units = ((payload.get("properties") or {}).get("meta") or {}).get("units") or {}
    unit = units.get("air_temperature")
    if unit not in (None, "celsius"):
        # No conversion is implemented (spec §5.7 is Celsius only): refuse the
        # payload rather than label Fahrenheit numbers as °C.
        raise ForecastFetchError(f"met.no sent air_temperature in {unit!r}, expected celsius")

    samples: list[dict] = []
    for entry in timeseries:
        data = (entry or {}).get("data") or {}
        instant = (data.get("instant") or {}).get("details") or {}
        temperature = instant.get("air_temperature")
        if temperature is None:
            continue
        precipitation, symbol = _precipitation_and_symbol(data)
        samples.append(
            {
                "time": entry.get("time"),
                "temperature": float(temperature),
                "precipitation": float(precipitation) if precipitation is not None else None,
                "symbol_code": symbol,
            }
        )
    if not samples:
        raise ForecastFetchError("met.no sent a timeseries with no temperature entries")
    return samples


def fetch_compact(
    *,
    lat: float,
    lon: float,
    user_agent: str = USER_AGENT,
    if_modified_since: str | None = None,
    url: str = FORECAST_URL,
    timeout: float = 25.0,
) -> tuple[int, dict | None, dict]:
    """One HTTP request to met.no.

    Returns ``(status, payload, headers)`` with status ``200`` (payload set) or
    ``304`` (payload None). Raises MetnoForbidden, MetnoRateLimited, or
    ForecastFetchError for everything else.
    """
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    if if_modified_since:
        headers["If-Modified-Since"] = if_modified_since
    request = urllib.request.Request(f"{url}?lat={lat:.4f}&lon={lon:.4f}", headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            received = {k.lower(): v for k, v in response.headers.items()}
            if response.status != 200:
                raise ForecastFetchError(f"met.no answered unexpected HTTP {response.status}")
            body = response.read()
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ForecastFetchError(f"met.no sent unreadable JSON: {exc}") from exc
            return 200, payload, received
    except urllib.error.HTTPError as exc:
        received = {k.lower(): v for k, v in (exc.headers or {}).items()}
        if exc.code == 304:
            return 304, None, received
        if exc.code == 403:
            raise MetnoForbidden(
                "met.no answered 403 Forbidden (its documented cause is a "
                f"rejected User-Agent; sent: {user_agent!r})"
            ) from exc
        if exc.code == 429:
            raise MetnoRateLimited(
                "met.no answered 429 Too Many Requests - backing off",
                retry_after=received.get("retry-after"),
            ) from exc
        raise ForecastFetchError(f"met.no answered HTTP {exc.code} {exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ForecastFetchError(f"met.no unreachable: {exc}") from exc


class ForecastCache:
    """The last good series, plus the poll loop that keeps it current.

    One instance per process. The server starts :meth:`run_forever` in a daemon
    thread and calls :meth:`view` from request threads; all state changes take
    the lock.
    """

    def __init__(
        self,
        *,
        place_id: str = PLACE_ID_DEFAULT,
        lat: float = LAT_DEFAULT,
        lon: float = LON_DEFAULT,
        cache_dir: Path | str | None = None,
        user_agent: str = USER_AGENT,
        url: str = FORECAST_URL,
        clock=time.time,
        update_interval_minutes: int = 15,
        log=_log,
    ) -> None:
        self.place_id = place_id
        # met.no caches on ~4 decimals; more precision is wasted and makes
        # two option spellings miss each other's cache.
        self.lat = round(float(lat), 4)
        self.lon = round(float(lon), 4)
        self.cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
        self.user_agent = user_agent
        self.url = url
        self.clock = clock
        self.update_interval_minutes = update_interval_minutes
        self.log = log

        self.samples: list[dict] = []
        self.fetched_at: float | None = None
        self.expires_at: float | None = None
        self.last_modified: str | None = None
        self.last_error: str | None = None
        self.last_outcome = "starting"
        self.failures = 0
        self._failure_delay = FAILURE_BACKOFF_START
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- cache

    def cache_path(self) -> Path:
        return self.cache_dir / CACHE_FILE

    def load(self) -> bool:
        """Restore the last good series from disk. True when one was found."""
        path = self.cache_path()
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            self.log(f"metno: no cache yet at {path} - first run")
            return False
        except (OSError, json.JSONDecodeError) as exc:
            self.log(f"metno: cache unreadable at {path}: {exc}")
            return False

        if raw.get("schema") != CACHE_SCHEMA:
            self.log(f"metno: cache schema {raw.get('schema')!r} is not {CACHE_SCHEMA}, ignoring")
            return False
        place = raw.get("place") or {}
        if (place.get("id"), place.get("lat"), place.get("lon")) != (self.place_id, self.lat, self.lon):
            self.log(
                f"metno: cache is for {place.get('id')} {place.get('lat')},{place.get('lon')}, "
                f"configured place is {self.place_id} {self.lat},{self.lon}; discarding"
            )
            return False

        with self._lock:
            self.samples = list(raw.get("samples") or [])
            self.fetched_at = raw.get("fetched_at")
            self.expires_at = raw.get("expires_at")
            self.last_modified = raw.get("last_modified")
            self.last_outcome = f"restored ({len(self.samples)} samples)"
        age = self.age_seconds()
        detail = f" (age {age / 60:.0f} min)" if age is not None else ""
        self.log(f"metno: restored {len(self.samples)} samples from {path}{detail}")
        return True

    def save(self) -> None:
        """Write the cache atomically - Supervisor may restart us mid-write."""
        document = {
            "schema": CACHE_SCHEMA,
            "place": {"id": self.place_id, "lat": self.lat, "lon": self.lon},
            "fetched_at": self.fetched_at,
            "expires_at": self.expires_at,
            "last_modified": self.last_modified,
            "samples": self.samples,
        }
        path = self.cache_path()
        tmp = path.with_name(path.name + ".tmp")
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(document, separators=(",", ":")))
            os.replace(tmp, path)
        except OSError as exc:
            self.log(f"metno: could not write cache to {path}: {exc}")

    # ------------------------------------------------------------------ view

    def age_seconds(self) -> int | None:
        """Seconds since the last successful fetch (a 304 counts), or None."""
        with self._lock:
            return self._age_locked()

    def stale(self) -> bool:
        """graph-spec §8.1: stale means older than 6 h. No cache is not stale
        - that is the §8.3 "noch keine Daten" case."""
        with self._lock:
            age = self._age_locked()
        return age is not None and age > STALE_AFTER_SECONDS

    def view(self) -> dict:
        """What the renderer and the status page read. Never fetches."""
        with self._lock:
            return {
                "place": {"id": self.place_id, "lat": self.lat, "lon": self.lon},
                "samples": list(self.samples),
                "fetched_at": self.fetched_at,
                "age_seconds": self._age_locked(),
                "stale": self._stale_locked(),
                "expires_at": self.expires_at,
                "last_outcome": self.last_outcome,
                "last_error": self.last_error,
                "cache_path": str(self.cache_path()),
            }

    # ------------------------------------------------------------- the poll

    def reconfigure(
        self,
        *,
        place_id: str,
        lat: float,
        lon: float,
        update_interval_minutes: int | None = None,
    ) -> bool:
        """Adopt live option changes. Returns True when the place changed.

        A place change drops the cached series (it is for another place) and
        makes the next poll immediate.
        """
        lat = round(float(lat), 4)
        lon = round(float(lon), 4)
        if update_interval_minutes is not None:
            self.update_interval_minutes = update_interval_minutes
        if (place_id, lat, lon) == (self.place_id, self.lat, self.lon):
            return False
        with self._lock:
            self.log(
                f"metno: place changed {self.place_id} {self.lat},{self.lon} -> "
                f"{place_id} {lat},{lon}; dropping cached series"
            )
            self.place_id, self.lat, self.lon = place_id, lat, lon
            self.samples = []
            self.fetched_at = None
            self.expires_at = None
            self.last_modified = None
            self.last_outcome = "place changed"
        return True

    def poll_once(self) -> str:
        """One poll attempt, honouring Expires. Never raises.

        Returns a word for the log: ``fresh`` (Expires not reached, no request
        made), ``updated`` (200), ``unchanged`` (304), or ``failed``.
        """
        with self._lock:
            if self._is_fresh_locked():
                remaining = int((self.expires_at or 0) - self.clock())
                self.last_outcome = f"fresh ({remaining}s left)"
                return "fresh"
            if_modified_since = self.last_modified

        try:
            status, payload, headers = fetch_compact(
                lat=self.lat,
                lon=self.lon,
                user_agent=self.user_agent,
                if_modified_since=if_modified_since,
                url=self.url,
            )
        except MetnoForbidden as exc:
            return self._fail(str(exc))
        except MetnoRateLimited as exc:
            return self._fail(str(exc), retry_after=exc.retry_after)
        except ForecastFetchError as exc:
            return self._fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - the poll loop must not die
            return self._fail(f"unexpected fetch error: {exc!r}")

        if status == 200:
            try:
                samples = normalise(payload or {})
            except ForecastFetchError as exc:
                return self._fail(str(exc))
            with self._lock:
                self.samples = samples
                self._stored_locked(headers, f"updated ({len(samples)} samples)")
            self.log(f"metno: 200 OK, {len(samples)} samples cached to {self.cache_path()}")
            return "updated"

        with self._lock:
            self._stored_locked(headers, f"unchanged ({len(self.samples)} samples)")
        self.log(f"metno: 304 Not Modified, series is still current ({len(self.samples)} samples)")
        return "unchanged"

    def run_forever(self, stop: threading.Event, reconfigure=None) -> None:
        """Poll loop for a daemon thread: poll, then sleep until Expires.

        ``reconfigure`` is called before each poll so option edits are picked
        up without a restart; the server passes its option reader. The loop
        swallows every error by design - a dead poller would leave the image
        frozen with no explanation.
        """
        self.load()
        while not stop.is_set():
            if reconfigure is not None:
                try:
                    reconfigure(self)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"metno: option reload failed: {exc!r}")
            self.poll_once()
            delay = max(MIN_SLEEP_SECONDS, min(self.sleep_hint(), MAX_SLEEP_SECONDS))
            stop.wait(delay)

    def sleep_hint(self) -> float:
        """Seconds until the next poll: until Expires, or the failure backoff."""
        with self._lock:
            if self.last_error is not None:
                return float(self._failure_delay)
            now = self.clock()
            if self.expires_at is not None and self.expires_at > now:
                return float(self.expires_at - now + 5)
            return float(self.update_interval_minutes * 60)

    # --------------------------------------------------------------- internals

    def _age_locked(self) -> int | None:
        if self.fetched_at is None:
            return None
        return max(0, int(self.clock() - self.fetched_at))

    def _stale_locked(self) -> bool:
        age = self._age_locked()
        return age is not None and age > STALE_AFTER_SECONDS

    def _is_fresh_locked(self) -> bool:
        """True when Expires says there is no point asking again."""
        return (
            bool(self.samples)
            and self.expires_at is not None
            and self.clock() < self.expires_at
        )

    def _stored_locked(self, headers: dict, outcome: str) -> None:
        """Book a successful response (200 or 304) - call with the lock held."""
        now = self.clock()
        expires_at = _epoch(headers.get("expires"))
        self.fetched_at = now
        self.expires_at = expires_at or (now + FALLBACK_POLL_SECONDS)
        self.last_modified = headers.get("last-modified") or self.last_modified
        self.last_error = None
        self.failures = 0
        self._failure_delay = FAILURE_BACKOFF_START
        self.last_outcome = outcome
        self.save()

    def _fail(self, message: str, retry_after: str | None = None) -> str:
        with self._lock:
            self.failures += 1
            self.last_error = message
            if retry_after and retry_after.strip().isdigit():
                self._failure_delay = min(int(retry_after.strip()), FAILURE_BACKOFF_MAX)
            else:
                self._failure_delay = min(
                    FAILURE_BACKOFF_START * (2 ** (self.failures - 1)),
                    FAILURE_BACKOFF_MAX,
                )
            self.last_outcome = f"failed: {message}"
            delay = self._failure_delay
        self.log(f"metno: {message} (retry in {delay}s)")
        return "failed"


def _cli(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="met.no client smoke test")
    parser.add_argument("--place", default=PLACE_ID_DEFAULT)
    parser.add_argument("--lat", type=float, default=LAT_DEFAULT)
    parser.add_argument("--lon", type=float, default=LON_DEFAULT)
    parser.add_argument("--cache-dir", default="/tmp/wettergraph-metno")
    parser.add_argument("--once", action="store_true", help="one poll, then exit")
    parser.add_argument("--watch", action="store_true", help="poll until Ctrl-C")
    args = parser.parse_args(argv)

    cache = ForecastCache(
        place_id=args.place,
        lat=args.lat,
        lon=args.lon,
        cache_dir=args.cache_dir,
        update_interval_minutes=1,
    )
    if args.watch:
        cache.run_forever(threading.Event())  # Ctrl-C ends it
        return 0
    cache.load()
    outcome = cache.poll_once()
    view = cache.view()
    print(json.dumps({k: v for k, v in view.items() if k != "samples"}, indent=2))
    print(f"outcome: {outcome}")
    print(f"samples: {len(view['samples'])}; first three:")
    print(json.dumps(view["samples"][:3], indent=2))
    return 0 if outcome != "failed" or view["samples"] else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
