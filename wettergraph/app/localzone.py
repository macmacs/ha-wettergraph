"""The app's local time zone, which the graph's hour and day labels are drawn in.

graph-spec §4.1 draws the time axis in "the app's local time zone", and the
renderer gets it the plain way, ``datetime.astimezone()``, i.e. from ``TZ``.
Supervisor is meant to hand every app ``TZ``, but on the operator's box the
labels came out in UTC (a 12:33 CEST render labelled its first column ``10``),
so ``TZ`` is not trusted to arrive.

The fallback is Home Assistant's own setting: ``time_zone`` in
``.storage/core.config``, readable through the ``homeassistant_config`` mapping
the app already has for ``/local/``. No new permission, and it follows the zone
the operator set in HA. Whichever wins is written back into ``TZ`` and
``time.tzset()`` is called, so every ``astimezone()`` in the process agrees.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

HA_CONFIG_DIR = Path(os.environ.get("WG_HA_CONFIG", "/homeassistant"))
CORE_CONFIG = ".storage/core.config"
FALLBACK = "UTC"


def _valid(zone: str | None) -> bool:
    if not zone:
        return False
    try:
        ZoneInfo(zone)
    except (ValueError, KeyError, OSError):
        return False
    return True


def ha_zone(config_dir: Path = HA_CONFIG_DIR) -> str | None:
    """``time_zone`` from HA's core.config, or None if it cannot be read."""
    try:
        doc = json.loads((config_dir / CORE_CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    zone = (doc.get("data") or {}).get("time_zone") if isinstance(doc, dict) else None
    return zone if isinstance(zone, str) else None


def resolve(environ=os.environ, config_dir: Path = HA_CONFIG_DIR) -> tuple[str, str]:
    """``(zone, source)``: ``TZ`` if it names a real zone, else HA's, else UTC."""
    zone = environ.get("TZ", "").strip()
    if _valid(zone):
        return zone, "TZ"
    zone = ha_zone(config_dir)
    if _valid(zone):
        return zone, f"{config_dir / CORE_CONFIG}"
    return FALLBACK, "fallback, neither TZ nor HA's core.config named a zone"


def apply(config_dir: Path = HA_CONFIG_DIR) -> tuple[str, str]:
    """Resolve the zone and make it the process's local time."""
    zone, source = resolve(config_dir=config_dir)
    os.environ["TZ"] = zone
    time.tzset()
    return zone, source
