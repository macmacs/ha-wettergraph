#!/usr/bin/env python3
"""Render the Wettergraph image (redesign ticket 05).

The graph is a clone of yr's meteogram with the wind band and the yr/NRK header
removed. Every number here is cloned, not designed: ``docs/graph-spec.md``
§0 lists each constant with its source in yr's own file, and the clause numbers
cited below are that spec's.

One render is one SVG drawn in the design canvas ``794.2373 x 210`` (§3.1) and
published at ``W x round(W * 210 / 794.2373)`` (§1.1). The scaling of §1.2 is
expressed once, as the ``viewBox``, instead of multiplied into every number:
that is exactly what ticket 04 measured the width range through, it keeps the
SVG crisp for the browser that now rescales it on the dashboard, and it makes a
clause-by-clause diff against ``graph-reference-v2.svg`` a direct comparison of
coordinates. ``preserveAspectRatio="none"`` so the canvas fills the intrinsic
box exactly and §1.4's opacity holds to the last row of pixels.

The PNG routes rasterise the same SVG through ``resvg-py``. No browser. The
font is loaded by file path, never by font discovery, because resvg draws every
text node as nothing when no font answers and reports no error (§3.4).

Input is the normalised series from :mod:`metno` - ``ForecastCache.view()``:
``samples``, ``fetched_at``, ``stale``, ``age_seconds``, ``last_error``. The
window is 60 h from the *render* hour, so its tail runs past met.no's hourly
entries on any render made after the poll; §4.8 interpolates it.

``show_age=True`` (previous map's ticket 06) draws the §8.2 age chip even when
the data is fresh, with minutes instead of hours under one hour. It is a debug
switch - §9.4 keeps the clock off the image otherwise - so a dashboard can be
watched proving it refreshes on its own.

Local check (needs the font file; this dev box has no fonts in the usual path)::

    uv run --with resvg-py python wettergraph/app/render.py \
        --cache /tmp/wg/forecast-cache.json --now <epoch> --out /tmp/wg/sample.png
"""

from __future__ import annotations

import argparse
import bisect
import functools
import html
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import resvg_py

import sun as sunmod

# ------------------------------------------------------------ §0 constants

STEP = 722.0 / 59.0  # 12.2373 px per hour: yr's 722 px plot over 59 intervals
WINDOW_HOURS = 60  # §4.1
WINDOW_POINTS = WINDOW_HOURS + 1  # 61
PLOT_W = WINDOW_HOURS * STEP  # 734.2373
PLOT_H = 120.0
GUTTER = 30.0  # §3.2, both sides
DESIGN_WIDTH = GUTTER + PLOT_W + GUTTER  # 794.2373 (§3.1)
DESIGN_HEIGHT = 210.0

DEFAULT_WIDTH = 794  # §10
MIN_WIDTH = 560  # §1.2, measured in ticket 04
MAX_WIDTH = 1588

ROW_DAY = 8.0  # §3.3: 8 margin, 24 day, 24 hour, 120 plot, 10 gap, 18 legend, 6
ROW_HOUR = 32.0
ROW_PLOT = 56.0
ROW_LEGEND = 186.0

GRID_DY = 12.0  # §4.3 horizontal cell grid
LABEL_DY = 24.0  # §3.5 axis label pitch, every second grid line
PX_PER_MM = 12.0  # §7.2, fixed 0..10 mm over the 120 px band
MM_MAX = 10.0
BAR_INSET = 0.5  # §7.4: x = i * STEP + 0.5, width = STEP - 1
BAR_WIDTH = STEP - 2 * BAR_INSET

CURVE_STROKE = 2.0  # §5.3
TEMP_LADDER = (1.0, 2.0, 3.0, 5.0, 10.0)  # §5.1 degrees per grid row
TEMP_ROWS = 10
ICON_BOX = 24.0  # §6.2
ICON_CLEARANCE = 5.0  # §6.3
ICON_HEADROOM = ICON_BOX + ICON_CLEARANCE  # 29 px reserved by the ladder (§5.1)
ICON_SAMPLES = 25  # the 24 px box, sampled every px, plus its right edge
EMPTY_BAND = (0.0, 3.0)  # §8.3 fallback band: 0..30, r = 3

DAY_LABEL_MIN_HOURS = 6.5  # §4.6
HOUR_LABEL_EVERY = 2  # §4.7
ICON_EVERY_HOURS = 2  # §6.1

F_DAY = 16.0  # §0 font sizes: yr's rem values at a 15 px root
F_SMALL = 13.0
F_OVERMAX = 12.0
DIGIT_ADVANCE = 1303.0 / 2048.0  # DejaVu Sans digit advance, for §7.5's chip

LEGEND_ENTRIES = (0.0, 126.0)  # §3.7, yr's own offsets minus the wind entry
SWATCH = 10.0

CHIP_WIDTH = 94.0  # §8.2
CHIP_HEIGHT = 18.0
CHIP_RADIUS = 3.0
CHIP_X = GUTTER + PLOT_W - CHIP_WIDTH  # 670.2373, right-aligned to the plot
CHIP_Y = ROW_LEGEND
STALE_MAX_HOURS = 48

# §4.9 day/night shading: the Android widget's ramp, one hour either side of
# each sunrise and sunset, capped so two ramps never overlap.
SHADE_RAMP_HOURS = 1.0

WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")  # §4.5

# §2.3, light and dark. Same geometry, only the palette changes (§2.1) - which
# is yr's own arrangement: ?mode=dark moves six fills and two gradient stops.
PALETTES = {
    "light": {
        "background": "#ffffff",
        "grid": "#c3d0d8",
        "separator": "#56616c",
        "day": "#21292b",
        "muted": "#56616c",
        "warm": "#c60000",
        "cold": "#006edb",
        "rain": "#006edb",
        "overmax": "#ffffff",
        "shade_day": ("#ffffff", 0.0),  # §4.9: light shades the night
        "shade_night": ("#21292b", 0.08),
    },
    "dark": {
        "background": "#020a14",
        "grid": "#374759",
        "separator": "#c3d0d8",  # the hex light mode uses for its *grid*
        "day": "#ffffff",
        "muted": "#a2a5b3",
        "warm": "#ff2d3f",
        "cold": "#00b8f1",
        "rain": "#00b8f1",
        "overmax": "#21292b",  # inverts: it is drawn on the bar, not the ground
        "shade_day": ("#c3d0d8", 0.08),  # §4.9: dark lifts the day instead
        "shade_night": ("#000000", 0.0),
    },
}

# §3.5: yr's own 24 px glyphs, lifted from meteogram-6325496.svg. yr tints them
# with a CSS filter; we set the fill directly, because resvg does no filter
# functions. Nothing reads yr.no at runtime - this is the art, not a fetch.
THERMOMETER = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="none" viewBox="0 0 24 24">'
    '<circle cx="12" cy="18" r="1.25" stroke="COLOUR" stroke-width="1.5"/>'
    '<path stroke="COLOUR" stroke-width="1.5" d="M12 17V8m0-5a3 3 0 0 0-3 3v9.354a4 4 0 1 0 6 0V6a3 3 0 0 0-3-3z"/>'
    "</svg>"
)
DROPLET = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="none" viewBox="0 0 24 24">'
    '<path fill="COLOUR" d="M2.04 12l-.747-.06.748.06zm19.92 0l.747-.06-.748.06zm-6.546 10.086l-.53-.53.53.53z'
    'm-2.828 0l-.53.53.53-.53zM2.788 12.062C3.221 6.78 7.235 2.75 12 2.75v-1.5c-5.668 0-10.221 4.757-10.707 '
    '10.69l1.495.122zM12 2.75c4.765 0 8.78 4.03 9.212 9.312l1.495-.123C22.22 6.007 17.668 1.25 12 1.25v1.5zm9 '
    '9.5H3v1.5h18v-1.5zm-19.707-.31c-.084 1.027.758 1.81 1.707 1.81v-1.5a.231.231 0 0 1-.166-.067.15.15 0 0 '
    '1-.046-.121l-1.495-.123zm19.919.122a.15.15 0 0 1-.046.121.231.231 0 0 1-.166.067v1.5c.949 0 1.79-.783 '
    '1.707-1.81l-1.495.122zM11.25 13v7.672h1.5V13h-1.5zm4.694 9.616l.586-.586-1.06-1.06-.586.585 1.06 1.061zm-3.889 '
    '0a2.75 2.75 0 0 0 3.89 0l-1.061-1.06a1.25 1.25 0 0 1-1.768 0l-1.06 1.06zm-.805-1.944c0 .729.29 1.428.805 '
    '1.944l1.061-1.06a1.25 1.25 0 0 1-.366-.884h-1.5z"/></svg>'
)

ICONS_DIR = Path(os.environ.get("WG_ICONS", Path(__file__).resolve().parent / "icons"))
# §3.4: the container path. WG_FONT exists so a box without fonts can still
# render a check (this development box has none at the container's path).
FONT_PATH = Path(os.environ.get("WG_FONT", "/usr/share/fonts/dejavu/DejaVuSans.ttf"))
# The family written into every text node. resvg matches the first name against
# the file above and never reaches the rest; the names after it are for a
# browser shown the SVG itself, which is now the normal case - the card loads
# the SVG, not the PNG (redesign ticket 00).
FONT_STACK = "DejaVu Sans, Verdana, sans-serif"

_font_warned = False


# --------------------------------------------------------------- small maths


def clamp_width(width) -> int:
    """§1.2: clamp 560..1588, never refuse. Non-numeric falls back to 794."""
    try:
        value = int(round(float(width)))
    except (TypeError, ValueError):
        value = DEFAULT_WIDTH
    return max(MIN_WIDTH, min(MAX_WIDTH, value))


def height_for(width: int) -> int:
    """§1.1/§1.3: the height follows the design canvas, it is never set."""
    return round(width * DESIGN_HEIGHT / DESIGN_WIDTH)


def _n(value: float) -> str:
    """A number as SVG text, deterministic and without trailing zeros."""
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _epoch(iso) -> int | None:
    if not iso:
        return None
    try:
        moment = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def _blend(before, after, fraction: float):
    """§4.8: linear between the two entries, or nothing if either is missing."""
    if before is None or after is None:
        return None
    return float(before) + (float(after) - float(before)) * fraction


def window_slots(samples, fetched_at=None, now=None) -> tuple[int, list[tuple[int, dict | None]]]:
    """§4.1/§4.8: the 61 hourly slots, keyed by epoch, with the window start.

    The window starts at the hour of the **render**, not of the fetch, so its
    tail runs past met.no's hourly entries on any render made an hour or more
    after the last poll. That is the normal case: a slot with no entry of its
    own is interpolated from the entries either side (the payload carries
    6-hourly ones out to ten days), and its symbol comes from the entry
    covering it. A slot outside the payload altogether stays a gap.
    """
    anchor = now if now is not None else (fetched_at if fetched_at is not None else time.time())
    start = int(float(anchor) // 3600) * 3600

    entries: list[tuple[int, dict]] = []
    for sample in samples or []:
        moment = _epoch((sample or {}).get("time"))
        if moment is not None:
            entries.append((moment, sample))
    entries.sort(key=lambda item: item[0])
    times = [moment for moment, _ in entries]

    slots: list[tuple[int, dict | None]] = []
    for i in range(WINDOW_POINTS):
        moment = start + i * 3600
        index = bisect.bisect_left(times, moment)
        if index < len(times) and times[index] == moment:
            slots.append((moment, entries[index][1]))
            continue
        if index == 0 or index >= len(times):
            slots.append((moment, None))  # outside the payload: a gap (§4.8)
            continue
        before, after = entries[index - 1][1], entries[index][1]
        fraction = (moment - times[index - 1]) / (times[index] - times[index - 1])
        slots.append(
            (
                moment,
                {
                    "time": datetime.fromtimestamp(moment, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "temperature": _blend(before.get("temperature"), after.get("temperature"), fraction),
                    "precipitation": _blend(before.get("precipitation"), after.get("precipitation"), fraction),
                    "symbol_code": before.get("symbol_code"),
                    "interpolated": True,
                },
            )
        )
    return start, slots


def temperature_band(temperatures) -> tuple[float, float]:
    """§5.1: the first ladder step that leaves 29 px of icon headroom.

    Returns ``(bottom, r)``: the band is always 120 px and ten rows, so the top
    is ``bottom + 10 * r`` and the scale is ``12 / r`` px per degree.
    """
    if not temperatures:
        return EMPTY_BAND
    low, high = min(temperatures), max(temperatures)
    for r in TEMP_LADDER:
        bottom = math.floor(low / r) * r
        top = bottom + TEMP_ROWS * r
        if (top - high) * (GRID_DY / r) >= ICON_HEADROOM:
            return bottom, r
    r = TEMP_LADDER[-1]  # §5.1: nothing fits, the icons clamp (§6.3)
    return math.floor(low / r) * r, r


# ------------------------------------------------------------------- spline


def _controls(points: list[tuple[float, float]], k: int):
    """yr's smoothing: Catmull-Rom to cubic Bézier, tension 1/6, ends
    duplicated (§0, §4.2). Verified against yr's first two segments."""
    p0 = points[k - 1] if k > 0 else points[0]
    p1, p2 = points[k], points[k + 1]
    p3 = points[k + 2] if k + 2 < len(points) else points[-1]
    c1 = (p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0)
    c2 = (p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0)
    return p1, c1, c2, p2


def _curve_path(points: list[tuple[float, float]]) -> str:
    out = [f"M{_n(points[0][0])} {_n(points[0][1])}"]
    for k in range(len(points) - 1):
        _, c1, c2, p2 = _controls(points, k)
        out.append(f"C{_n(c1[0])} {_n(c1[1])}, {_n(c2[0])} {_n(c2[1])}, {_n(p2[0])} {_n(p2[1])}")
    return "".join(out)


def _curve_y(points: list[tuple[float, float]], x: float) -> float:
    """The curve's height at x, sampled off the same spline the path draws."""
    k = max(0, min(int((x - points[0][0]) / STEP), len(points) - 2))
    p1, c1, c2, p2 = _controls(points, k)
    best = None
    for q in range(101):
        t = q / 100.0
        u = 1.0 - t
        xx = u**3 * p1[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t**3 * p2[0]
        yy = u**3 * p1[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t**3 * p2[1]
        if best is None or abs(xx - x) < best[0]:
            best = (abs(xx - x), yy)
    return best[1]


def _runs(readings: list[tuple[int, float, float]]) -> list[list[tuple[int, float, float]]]:
    """Split the points into runs of consecutive hours, so a §4.8 gap breaks
    the curve instead of joining across it."""
    runs: list[list[tuple[int, float, float]]] = []
    run: list[tuple[int, float, float]] = []
    previous = None
    for item in readings:
        if previous is not None and item[0] == previous + 1:
            run.append(item)
        else:
            if run:
                runs.append(run)
            run = [item]
        previous = item[0]
    if run:
        runs.append(run)
    return runs


# -------------------------------------------------------------------- icons


@functools.lru_cache(maxsize=8)
def _icon_index(icons_dir: str) -> dict[str, str]:
    """``symbol_code`` -> icon file name, from the previous map's ``index.json``."""
    try:
        document = json.loads((Path(icons_dir) / "index.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(code): str(entry["file"])
        for code, entry in (document.get("icons") or {}).items()
        if isinstance(entry, dict) and entry.get("file")
    }


@functools.lru_cache(maxsize=256)
def _icon_text(path: str) -> str:
    return Path(path).read_text()


def _icon_for(code: str | None, icons_dir: Path) -> str | None:
    if not code:
        return None
    name = _icon_index(str(icons_dir)).get(str(code))
    if not name:
        return None
    try:
        return _icon_text(str(Path(icons_dir) / name))
    except OSError:
        return None


_OPEN_SVG = re.compile(r"^.*?<svg\b[^>]*>", re.S)
_ID = re.compile(r'\bid="([^"]*)"')
_URL = re.compile(r"url\(#([^)]*)\)")
_HREF = re.compile(r'href="#([^"]*)"')


def _inner_svg(text: str) -> str:
    """Drop the icon file's own ``<svg>`` wrapper; the art hangs on a ``<g>``."""
    match = _OPEN_SVG.search(text)
    inner = text[match.end():] if match else text
    return inner.rsplit("</svg>", 1)[0]


def _prefix_ids(text: str, prefix: str) -> str:
    """Namespace one icon's ids, so the same symbol can appear twice in a row."""
    text = _ID.sub(lambda m: f'id="{prefix}{m.group(1)}"', text)
    text = _URL.sub(lambda m: f"url(#{prefix}{m.group(1)})", text)
    return _HREF.sub(lambda m: f'href="#{prefix}{m.group(1)}"', text)


def _inline_icon(text: str, prefix: str, x: float, y: float) -> str:
    """§6.2: the art is a 100-unit square, so a 24 px box is scale(0.24)."""
    inner = _prefix_ids(_inner_svg(text), prefix)
    return f'<g transform="translate({_n(x)} {_n(y)}) scale({_n(ICON_BOX / 100.0)})">{inner}</g>'


# ------------------------------------------------------------------- stale


def stale_label(age_seconds) -> str:
    """§8.2: hours up to 48, then days."""
    age = max(0.0, float(age_seconds or 0.0))
    hours = round(age / 3600.0)
    if hours <= STALE_MAX_HOURS:
        return f"vor {hours} h"
    days = round(age / 86400.0)
    return f"vor {days} Tagen"


def age_label(age_seconds) -> str:
    """The same chip while the data is fresh: §8.2's wording, minutes first.

    Only reached with ``show_age=True``; a fresh graph carries no chip.
    """
    age = max(0.0, float(age_seconds or 0.0))
    if age < 3600:
        return f"vor {int(age // 60)} min"
    return stale_label(age)


# ------------------------------------------------------------------- shade


def _shade(sun, start: int, palette: dict) -> str | None:
    """§4.9: the plot band tinted by daylight, ramped over each sun event."""
    phases = sunmod.transitions(sun, start - 2 * 3600 * SHADE_RAMP_HOURS, start + (WINDOW_HOURS + 2) * 3600)
    if phases is None:
        return None
    day, events = phases
    colours = {True: palette["shade_day"], False: palette["shade_night"]}
    if not events and colours[day][1] <= 0:
        return None

    def stop(x: float, is_day: bool) -> str:
        colour, opacity = colours[is_day]
        offset = min(max(x / PLOT_W, 0.0), 1.0) * 100.0
        return f'<stop offset="{_n(offset)}%" stop-color="{colour}" stop-opacity="{_n(opacity)}"/>'

    xs = [(moment - start) / 3600.0 * STEP for moment, _ in events]
    stops = [stop(0.0, day)]
    for k, ((_, is_day), x) in enumerate(zip(events, xs)):
        ramp = SHADE_RAMP_HOURS * STEP
        if k > 0:
            ramp = min(ramp, (x - xs[k - 1]) / 2.0)
        if k + 1 < len(xs):
            ramp = min(ramp, (xs[k + 1] - x) / 2.0)
        stops.append(stop(x - ramp, not is_day))
        stops.append(stop(x + ramp, is_day))
    return (
        f'<defs><linearGradient id="day-night-gradient" x1="0" y1="0" x2="{_n(PLOT_W)}" y2="0"'
        f' gradientUnits="userSpaceOnUse">{"".join(stops)}</linearGradient></defs>'
        f'<rect x="0" y="0" width="{_n(PLOT_W)}" height="{_n(PLOT_H)}" fill="url(#day-night-gradient)"/>'
    )


# --------------------------------------------------------------------- svg


def build_svg(
    samples,
    *,
    fetched_at=None,
    now=None,
    stale: bool = False,
    age_seconds=None,
    last_error: str | None = None,
    width: int = DEFAULT_WIDTH,
    theme: str = "light",
    icons_dir: Path | str | None = None,
    show_age: bool = False,
    sun: dict | None = None,
) -> str:
    """The whole image as one SVG. Deterministic for the same arguments.

    ``sun`` is a ``sun.sun`` reading (see :mod:`sun`); ``None`` draws no
    day/night shading (§4.9).
    """
    width = clamp_width(width)
    height = height_for(width)
    palette = PALETTES.get(str(theme), PALETTES["light"])  # §2.2 unknown -> light
    icons_dir = Path(icons_dir) if icons_dir is not None else ICONS_DIR
    start, slots = window_slots(samples, fetched_at, now)

    out: list[str] = [
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
        f' width="{width}" height="{height}" viewBox="0 0 {_n(DESIGN_WIDTH)} {_n(DESIGN_HEIGHT)}"'
        ' preserveAspectRatio="none">',
        # §1.4: opaque, no rounded corners, no border of its own. The card frames it.
        f'<rect x="0" y="0" width="{_n(DESIGN_WIDTH)}" height="{_n(DESIGN_HEIGHT)}" fill="{palette["background"]}"/>',
    ]

    def text(x, y, value, *, size, colour, anchor=None, weight=None) -> str:
        extra = f' text-anchor="{anchor}"' if anchor else ""
        extra += f' font-weight="{_n(weight)}"' if weight else ""
        return (
            f'<text x="{_n(x)}" y="{_n(y)}" dy="0.35em" font-family="{FONT_STACK}"'
            f' font-size="{_n(size)}"{extra} fill="{colour}">{html.escape(str(value))}</text>'
        )

    locals_ = [datetime.fromtimestamp(moment).astimezone() for moment, _ in slots]

    # §4.5/§4.6 day labels: the window's opening hour, then every local
    # midnight; dropped where fewer than 6.5 h remain before the next boundary
    # or the window end, which is the no-overlap and no-clip condition at both
    # edges (a label is 69-76 px of ink, 6.5 h is 79.5 px).
    boundaries = [0] + [i for i, moment in enumerate(locals_) if i and moment.hour == 0]
    out.append(f'<g transform="translate({_n(GUTTER)} {_n(ROW_DAY)})">')
    for position, i in enumerate(boundaries):
        following = boundaries[position + 1] if position + 1 < len(boundaries) else WINDOW_HOURS
        if following - i < DAY_LABEL_MIN_HOURS:
            continue
        moment = locals_[i]
        label = f"{WEEKDAYS[moment.weekday()]} {moment.day:02d}.{moment.month:02d}."
        out.append(text(i * STEP, 12, label, size=F_DAY, colour=palette["day"], weight=600))
    out.append("</g>")

    # §4.7 hour labels, every even point index except the last: the final
    # point's label would sit on the mm axis.
    out.append(f'<g transform="translate({_n(GUTTER)} {_n(ROW_HOUR)})">')
    for i in range(0, WINDOW_HOURS, HOUR_LABEL_EVERY):
        out.append(
            text(i * STEP, 9, f"{locals_[i].hour:02d}", size=F_SMALL, colour=palette["muted"], anchor="middle")
        )
    out.append("</g>")

    # ---------------------------------------------------------- the one band
    out.append(f'<g transform="translate({_n(GUTTER)} {_n(ROW_PLOT)})">')

    # §4.9 day/night shading, behind the grid: one rect, one horizontal gradient.
    shade = _shade(sun, start, palette)
    if shade:
        out.append(shade)

    # §4.3 the cell grid, behind everything else: 11 horizontal lines, one vertical
    # per point. §4.4 a local midnight replaces its grid line with a separator,
    # it does not add one. The outermost grid lines are the frame.
    for row in range(int(PLOT_H // GRID_DY) + 1):
        y = row * GRID_DY
        out.append(f'<line x1="0" x2="{_n(PLOT_W)}" y1="{_n(y)}" y2="{_n(y)}" stroke="{palette["grid"]}"/>')
    midnights = set(boundaries[1:])
    for i in range(WINDOW_POINTS):
        x = _n(i * STEP)
        colour = palette["separator"] if i in midnights else palette["grid"]
        out.append(f'<line x1="{x}" x2="{x}" y1="0" y2="{_n(PLOT_H)}" stroke="{colour}"/>')

    readings = [
        (i, i * STEP, float(slot["temperature"]))
        for i, (_, slot) in enumerate(slots)
        if slot and slot.get("temperature") is not None
    ]
    bottom, degrees_per_row = temperature_band([value for _, _, value in readings])
    per_degree = GRID_DY / degrees_per_row  # §5.1

    def y_temp(value: float) -> float:
        return PLOT_H - (value - bottom) * per_degree

    # §3.5 the two axis gutters, each label centred on its grid line. §3.6: a
    # band bottom at -10 °C or below drops the degree sign from every label of
    # this render, because "-10°" is 27.7 px and would clip the canvas edge.
    degree_sign = "" if bottom <= -10 else "°"
    out.append(f'<g transform="translate(-17, -6) scale(0.5)">{THERMOMETER.replace("COLOUR", palette["muted"])}</g>')
    out.append(
        f'<g transform="translate({_n(PLOT_W + 5)}, -6) scale(0.5)">{DROPLET.replace("COLOUR", palette["muted"])}</g>'
    )
    for row in range(1, int(PLOT_H // LABEL_DY) + 1):
        y = row * LABEL_DY
        celsius = bottom + (PLOT_H - y) / per_degree
        out.append(
            text(-5, y, f"{_n(celsius)}{degree_sign}", size=F_SMALL, colour=palette["muted"], anchor="end")
        )
        out.append(
            text(PLOT_W + 5, y, _n((PLOT_H - y) / PX_PER_MM), size=F_SMALL, colour=palette["muted"])
        )

    # §7 precipitation: one bar per hourly interval, 60 for 61 points, on the
    # fixed 0..10 mm axis, behind the curve and the icons (§7.1, §7.3).
    overmax: list[tuple[int, float]] = []
    for i in range(WINDOW_HOURS):
        slot = slots[i][1]
        amount = (slot or {}).get("precipitation")
        rate = max(0.0, float(amount)) if amount is not None else 0.0
        if rate <= 0:  # §7.6: no value draws no bar, exactly like a 0.0
            continue
        drawn = min(rate * PX_PER_MM, PLOT_H)  # §7.5 clipped to the band top
        out.append(
            f'<rect x="{_n(i * STEP + BAR_INSET)}" y="{_n(PLOT_H - drawn)}" width="{_n(BAR_WIDTH)}"'
            f' height="{_n(drawn)}" fill="{palette["rain"]}"/>'
        )
        if rate > MM_MAX:
            overmax.append((i, rate))

    # §5.5 warm and cold are one path with one gradient, not two paths: two
    # coincident stops at y(0 °C), which pad to a single colour when the offset
    # leaves 0..100 % - the correct result for an all-warm or all-frozen window.
    if readings:
        zero = y_temp(0.0) / PLOT_H * 100.0
        out.append(
            f'<defs><linearGradient id="temperature-curve-gradient" x1="0" y1="0" x2="0" y2="{_n(PLOT_H)}"'
            ' gradientUnits="userSpaceOnUse" spreadMethod="pad">'
            f'<stop offset="{_n(zero)}%" stop-color="{palette["warm"]}"/>'
            f'<stop offset="{_n(zero)}%" stop-color="{palette["cold"]}"/>'
            f'<stop offset="100%" stop-color="{palette["cold"]}"/></linearGradient></defs>'
        )
    runs = [[(x, y_temp(value)) for _, x, value in run] for run in _runs(readings)]
    spans = [(run[0][0], run[-1][0]) for run in runs]
    for run in runs:
        if len(run) < 2:
            continue  # §4.8: a lone point between two gaps draws no segment
        out.append(
            f'<path d="{_curve_path(run)}" fill="none" stroke="url(#temperature-curve-gradient)"'
            f' stroke-width="{_n(CURVE_STROKE)}"/>'
        )

    # §7.5 the over-max value, printed on the clipped bar with a bar-coloured
    # chip behind it: two digits are 15.3 px of ink on an 11.2373 px bar, and
    # without the chip the overflow lands on the background whenever the
    # neighbouring hours are dry.
    for i, rate in overmax:
        label = str(int(round(rate)))
        centre = i * STEP + STEP / 2.0
        ink = len(label) * DIGIT_ADVANCE * F_OVERMAX
        out.append(
            f'<rect x="{_n(centre - ink / 2.0 - 2)}" y="0" width="{_n(ink + 4)}" height="{_n(F_OVERMAX)}"'
            f' fill="{palette["rain"]}"/>'
        )
        out.append(text(centre, 6, label, size=F_OVERMAX, colour=palette["overmax"], anchor="middle"))

    # §6 icons, drawn last, over the bars and the curve: 24 px every 2 h,
    # centred on the odd point index, the bottom 5 px above the curve's highest
    # point across the box's own width, clamped to the plot top.
    for i in range(1, WINDOW_HOURS, ICON_EVERY_HOURS):
        slot = slots[i - 1][1]  # §6.4: the cell's first hour carries the symbol
        icon = _icon_for((slot or {}).get("symbol_code"), icons_dir)
        if icon is None:
            continue
        left = i * STEP - ICON_BOX / 2.0
        run = next((r for r, (x0, x1) in zip(runs, spans) if x0 <= i * STEP <= x1 and len(r) > 1), None)
        if run is None:
            continue
        x0, x1 = run[0][0], run[-1][0]
        highest = min(_curve_y(run, min(max(left + q, x0), x1)) for q in range(ICON_SAMPLES))
        out.append(
            _inline_icon(icon, prefix=f"wg{i}_", x=left, y=max(0.0, highest - ICON_HEADROOM))
        )

    # §8.3 nothing was ever fetched: the whole frame is drawn above, because
    # every part of it comes from the clock alone. Only the data is missing.
    if not readings:
        out.append(
            text(PLOT_W / 2.0, PLOT_H / 2.0, "noch keine Daten", size=F_DAY, colour=palette["day"], anchor="middle")
        )
        if last_error:
            out.append(
                text(
                    PLOT_W / 2.0, PLOT_H / 2.0 + 20, str(last_error)[:90],
                    size=F_OVERMAX, colour=palette["muted"], anchor="middle",
                )
            )
    out.append("</g>")

    # §3.7 the legend row: yr's own two entries, the wind one dropped.
    out.append(f'<g transform="translate({_n(GUTTER)}, {_n(ROW_LEGEND)})">')
    for offset, colour, swatch, caption in (
        (LEGEND_ENTRIES[0], palette["warm"], f'<rect x="0" y="40%" width="100%" height="20%" fill="{palette["warm"]}"/>', "Temperatur °C"),
        (LEGEND_ENTRIES[1], palette["rain"], f'<rect x="0" y="0" width="100%" height="100%" fill="{palette["rain"]}"/>', "Niederschlag mm"),
    ):
        out.append(
            f'<g transform="translate({_n(offset)}, 0)">'
            f'<svg x="0" y="4" width="{_n(SWATCH)}" height="{_n(SWATCH)}">{swatch}</svg>'
            + text(14, 9, caption, size=F_SMALL, colour=palette["muted"])
            + "</g>"
        )
    out.append("</g>")

    # §8.2 the age chip, right-aligned to the plot in the legend row, over
    # whatever is under it (§8.4: nothing moves, no row resizes). show_age
    # forces the same chip on fresh data.
    age = age_seconds
    if age is None and fetched_at is not None and now is not None:
        age = max(0.0, float(now) - float(fetched_at))
    if (stale or show_age) and age is not None:
        out.append(
            f'<rect x="{_n(CHIP_X)}" y="{_n(CHIP_Y)}" width="{_n(CHIP_WIDTH)}" height="{_n(CHIP_HEIGHT)}"'
            f' rx="{_n(CHIP_RADIUS)}" fill="{palette["background"]}" fill-opacity="0.85"/>'
        )
        out.append(
            f'<circle cx="{_n(CHIP_X + 8)}" cy="{_n(CHIP_Y + 9)}" r="3" fill="{palette["muted"]}"/>'
        )
        out.append(
            f'<text x="{_n(CHIP_X + 16)}" y="{_n(CHIP_Y + 13)}" font-family="{FONT_STACK}"'
            f' font-size="{_n(F_OVERMAX)}" fill="{palette["muted"]}">'
            f"{html.escape(stale_label(age) if stale else age_label(age))}</text>"
        )

    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- rasterising


def _font_file(font_path: Path | str | None = None) -> str | None:
    """The font file resvg may use. Missing -> log once and render no text."""
    global _font_warned
    path = Path(font_path) if font_path is not None else FONT_PATH
    if path.is_file():
        return str(path)
    if not _font_warned:
        print(
            f"wettergraph: render font {path} is not readable; "
            "resvg draws no text at all (graph-spec §3.4)",
            flush=True,
        )
        _font_warned = True
    return None


def _rasterise(svg: str, width: int, font_path: Path | str | None = None) -> bytes:
    font = _font_file(font_path)
    return resvg_py.svg_to_bytes(
        svg_string=svg,
        width=width,
        height=height_for(width),
        font_files=[font] if font else None,
        skip_system_fonts=True,
    )


def render_png(samples, *, font_path: Path | str | None = None, **kwargs) -> bytes:
    """Render and return PNG bytes. Same arguments -> byte-identical PNG."""
    width = clamp_width(kwargs.get("width", DEFAULT_WIDTH))
    return _rasterise(build_svg(samples, **kwargs), width, font_path)


def _view_arguments(view: dict, *, width, theme, now, icons_dir, show_age=False, sun=None) -> dict:
    view = view or {}
    return dict(
        samples=view.get("samples") or [],
        fetched_at=view.get("fetched_at"),
        now=now,
        stale=bool(view.get("stale")),
        age_seconds=view.get("age_seconds"),
        last_error=view.get("last_error"),
        width=width,
        theme=theme,
        icons_dir=icons_dir,
        show_age=show_age,
        sun=sun,
    )


def build_view_svg(
    view: dict, *, width=DEFAULT_WIDTH, theme="light", now=None, icons_dir=None, show_age=False, sun=None
) -> str:
    """The view from :meth:`metno.ForecastCache.view`, as SVG text."""
    return build_svg(
        **_view_arguments(
            view, width=width, theme=theme, now=now, icons_dir=icons_dir, show_age=show_age, sun=sun
        )
    )


def render_view(
    view: dict,
    *,
    width=DEFAULT_WIDTH,
    theme="light",
    now=None,
    icons_dir=None,
    font_path=None,
    show_age=False,
    sun=None,
) -> bytes:
    """The view from :meth:`metno.ForecastCache.view`, as a PNG."""
    arguments = _view_arguments(
        view, width=width, theme=theme, now=now, icons_dir=icons_dir, show_age=show_age, sun=sun
    )
    return render_png(font_path=font_path, **arguments)


# ----------------------------------------------------------------------- cli


def fixture_samples(start: float, hours: int = 72, *, dry: bool = False) -> list[dict]:
    """A synthetic series for local renders, so a check needs no network."""
    every = ("clearsky_day", "partlycloudy_day", "cloudy", "lightrain", "clearsky_night", "fair_night")
    start = int(float(start) // 3600) * 3600  # window_slots anchors on the hour
    samples = []
    for i in range(hours):
        moment = start + i * 3600
        temperature = 9.0 + 7.0 * math.sin((i - 7) / 24.0 * 2.0 * math.pi) + 1.6 * math.sin(i / 5.0)
        rain = 0.0
        if not dry and 10 <= i % 24 <= 14:
            rain = 0.6 + 0.35 * ((i % 24) - 10)
        samples.append(
            {
                "time": datetime.fromtimestamp(moment, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "temperature": round(temperature, 1),
                "precipitation": round(rain, 2),
                "symbol_code": every[(i // 3) % len(every)],
            }
        )
    return samples


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="render a Wettergraph image")
    parser.add_argument("--cache", help="forecast-cache.json to read; without it, a fixture is rendered")
    parser.add_argument("--out", default="wettergraph-sample.png")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--theme", default="light", choices=("light", "dark"))
    parser.add_argument("--now", type=float, help="epoch seconds, for a reproducible window and chip")
    parser.add_argument("--dry", action="store_true", help="fixture with no precipitation")
    parser.add_argument("--age-hours", type=float, help="draw the stale chip with this age")
    parser.add_argument("--daynight", action="store_true", help="shade night, sunrise 07:00, sunset 19:00")
    parser.add_argument("--font", default=str(FONT_PATH))
    parser.add_argument("--icons", default=str(ICONS_DIR))
    arguments = parser.parse_args(argv)

    if arguments.cache:
        document = json.loads(Path(arguments.cache).read_text())
        samples = document.get("samples") or []
        fetched_at = document.get("fetched_at")
    else:
        fetched_at = arguments.now if arguments.now is not None else time.time()
        samples = fixture_samples(fetched_at, dry=arguments.dry)

    age_seconds = arguments.age_hours * 3600 if arguments.age_hours is not None else None
    kwargs = dict(
        fetched_at=fetched_at,
        now=arguments.now,
        stale=age_seconds is not None,
        age_seconds=age_seconds,
        last_error=None,
        width=arguments.width,
        theme=arguments.theme,
        icons_dir=arguments.icons,
        sun=sunmod.fixture(arguments.now or time.time()) if arguments.daynight else None,
    )
    if str(arguments.out).endswith(".svg"):
        Path(arguments.out).write_text(build_svg(samples, **kwargs))
        size = len(Path(arguments.out).read_text())
    else:
        png = render_png(samples, font_path=arguments.font, **kwargs)
        Path(arguments.out).write_bytes(png)
        size = len(png)
    width = clamp_width(arguments.width)
    print(
        f"rendered {len(samples)} samples -> {arguments.out} ({size} bytes, "
        f"theme={arguments.theme}, {width}x{height_for(width)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
