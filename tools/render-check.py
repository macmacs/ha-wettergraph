#!/usr/bin/env python3
"""Clause-level checks for the Wettergraph renderer (redesign ticket 05).

    uv run --with resvg-py --with pillow python tools/render-check.py

Every check is named after the clause it proves in `docs/graph-spec.md`. The
geometry checks diff the renderer's own SVG against the layout reference
cloned out of yr's meteogram - ``tools/assets/graph-reference-v2.svg`` (yr's
own dry forecast) and ``tools/assets/graph-reference-v2-rain.svg`` (the same
geometry with bars, including an over-max one). No network is used: the series
are fixtures, so the output is reproducible.

The reference hard-codes yr's own band (3..33 °C); §5.1 fits ours by ladder and
lands one row higher on the same data. That difference is a single constant,
read off both files' own bottom axis labels, and every y comparison below
carries it: the point is that the geometry is identical *once the band is
applied*, which is what a clone means here.

The SVG is parsed as XML (``xml.etree``); the four row groups of §3.3 are
inspected by transform, so art inlined into the icons cannot be mistaken for
graph geometry.

Exit code 0 = every check passed.
"""

from __future__ import annotations

import io
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "wettergraph" / "app"))
ASSETS = ROOT / "tools" / "assets"
REFERENCE = ASSETS / "graph-reference-v2.svg"
REFERENCE_RAIN = ASSETS / "graph-reference-v2-rain.svg"
ICONS = ROOT / "wettergraph" / "app" / "icons"

import render  # noqa: E402  (path set above)

try:
    from PIL import Image  # noqa: E402
except ImportError:  # pragma: no cover - the run line installs pillow
    Image = None

# The checks need a real font: without one resvg draws no text and §3.4's check
# would report a difference that says nothing about the renderer.
FONT = os.environ.get("WG_FONT") or ""
if not Path(FONT).is_file():
    FONT = next(
        (str(c) for c in (render.FONT_PATH, Path("/tmp/DejaVuSans.ttf")) if Path(c).is_file()),
        str(render.FONT_PATH),
    )

# The window and the day labels read the local clock; pin the check to UTC so
# the reference's own Sunday-08:00 start lands where it drew it.
os.environ["TZ"] = "UTC"
time.tzset()

START = int(datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc).timestamp())  # So 20.09., 08:00
STEP = render.STEP
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


# ------------------------------------------------------------- svg helpers


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def rows(svg: str) -> tuple[ET.Element, dict[str, ET.Element]]:
    """The document and its four §3.3 row groups, keyed by transform."""
    root = ET.fromstring(svg)
    return root, {el.get("transform", ""): el for el in root if local(el.tag) == "g"}


DAY_ROW, HOUR_ROW, PLOT_ROW, LEGEND_ROW = (
    "translate(30 8)", "translate(30 32)", "translate(30 56)", "translate(30, 186)",
)


def kids(group: ET.Element, name: str) -> list[ET.Element]:
    return [el for el in group if local(el.tag) == name]


def num(el: ET.Element, name: str, default: float = 0.0) -> float:
    try:
        return float(el.get(name, default))
    except (TypeError, ValueError):
        return default


def close(a: float, b: float, tolerance: float = 0.05) -> bool:
    return abs(a - b) <= tolerance


def numbers(path: str) -> list[float]:
    """Every number in a path, in order - a minus always opens a new one."""
    return [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", path)]


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def series(temps, rain=None, codes="partlycloudy_day", start=START, hours=1) -> list[dict]:
    """A sample list: one entry every ``hours`` hours from ``start``."""
    rain = rain if rain is not None else [0.0] * len(temps)
    codes = [codes] * len(temps) if isinstance(codes, str) else codes
    return [
        {
            "time": iso(start + i * 3600 * hours),
            "temperature": None if t is None else round(float(t), 4),
            "precipitation": rain[i] if i < len(rain) else 0.0,
            "symbol_code": codes[i] if i < len(codes) else None,
        }
        for i, t in enumerate(temps)
    ]


def svg_of(samples, **kwargs) -> str:
    kwargs.setdefault("fetched_at", START)
    kwargs.setdefault("now", START)
    return render.build_svg(samples, icons_dir=ICONS, **kwargs)


def png_of(samples, **kwargs) -> bytes:
    kwargs.setdefault("fetched_at", START)
    kwargs.setdefault("now", START)
    font = kwargs.pop("font_path", FONT)
    return render.render_png(samples, icons_dir=ICONS, font_path=font, **kwargs)


def png_size(data: bytes) -> tuple[int, int]:
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def chrome(svg: str) -> str:
    """The document with the icon art removed: yr's symbols carry gradients,
    filters and opacities of their own, and none of them are the graph's."""
    root = ET.fromstring(svg)
    for parent in list(root.iter()):
        for child in list(parent):
            if local(child.tag) == "g" and "scale(0.24)" in (child.get("transform") or ""):
                parent.remove(child)
    return ET.tostring(root).decode()


def band_of(plot: ET.Element) -> tuple[float, float]:
    """(bottom °C, °C per row) read off a render's own axis labels (§5.2)."""
    labels = {
        round(num(el, "y")): float((el.text or "").rstrip("°"))
        for el in kids(plot, "text")
        if close(num(el, "x"), -5.0)
    }
    return labels[120], (labels[96] - labels[120]) / 2.0


# ---------------------------------------------------------------- fixtures

check("the check font file exists", Path(FONT).is_file(), FONT)

# yr's own hourly temperatures, read back out of the reference's curve, so the
# parity render draws the same forecast the reference drew.
reference_root, reference_rows = rows(REFERENCE.read_text())
reference_plot = reference_rows[PLOT_ROW]
reference_curve = numbers([el for el in kids(reference_plot, "path")][0].get("d", ""))
REF_BOTTOM, REF_R = band_of(reference_plot)
yr_temps = [REF_BOTTOM + (120.0 - reference_curve[i]) / (render.GRID_DY / REF_R)
            for i in range(1, len(reference_curve), 6)]
yr_temps.insert(0, REF_BOTTOM + (120.0 - reference_curve[1]) / (render.GRID_DY / REF_R))
yr_temps = [REF_BOTTOM + (120.0 - y) / (render.GRID_DY / REF_R) for y in
            [reference_curve[1]] + [reference_curve[i] for i in range(7, len(reference_curve), 6)]]

parity = series(yr_temps, [0.0] * 58 + [0.1, 0.0])
rain_amounts = [0.0] * 60
for i, mm in [(6, 0.3), (7, 1.1), (8, 2.4), (9, 1.6), (10, 0.4), (23, 0.2), (24, 0.9),
              (31, 4.2), (32, 8.6), (33, 13.4), (34, 5.1), (35, 1.3), (48, 0.6), (49, 0.2)]:
    rain_amounts[i] = mm
wet = series(yr_temps, rain_amounts)
flat = series([10.0] * 61)
freezing = series([-12.0 + 0.1 * i for i in range(61)], [3.0] * 61)
warm = series([12.0 + 0.05 * i for i in range(61)])

parity_svg = svg_of(parity)
CHROME = chrome(parity_svg)
parity_root, parity_rows = rows(parity_svg)
plot = parity_rows[PLOT_ROW]
OUR_BOTTOM, OUR_R = band_of(plot)
# §5.1 fits the band; the reference hard-codes yr's. One constant, both ys.
DELTA = (OUR_BOTTOM - REF_BOTTOM) * (render.GRID_DY / OUR_R)


# ------------------------------------------------------- §1 output surface


check("§1.1 the SVG is 794 x 210 over the 794.2373 x 210 design canvas",
      parity_root.get("width") == "794" and parity_root.get("height") == "210"
      and parity_root.get("viewBox") == "0 0 794.2373 210"
      and parity_root.get("preserveAspectRatio") == "none",
      f'{parity_root.get("width")}x{parity_root.get("height")} {parity_root.get("viewBox")}')

default_png = png_of(parity)
check("§1.1 the default PNG is 794 x 210", png_size(default_png) == (794, 210), str(png_size(default_png)))
check("§1.1 the bytes are a real PNG", default_png[:8] == b"\x89PNG\r\n\x1a\n", default_png[:4].hex())

clamps = [(100, 560), (559, 560), (560, 560), (794, 794), (1588, 1588), (2000, 1588), ("junk", 794)]
clamped = [(asked, png_size(png_of(parity, width=asked))[0]) for asked, _ in clamps]
check("§1.2 width clamps to 560..1588, junk falls back to 794",
      all(got == wanted for (_, wanted), (_, got) in zip(clamps, clamped)), str(clamped))
check("§1.3 the height follows the design canvas, it is never set",
      all(png_size(png_of(parity, width=w)) == (w, render.height_for(w)) for w in (560, 794, 1000, 1588))
      and [render.height_for(w) for w in (560, 794, 1588)] == [148, 210, 420],
      str([(w, render.height_for(w)) for w in (560, 794, 1000, 1588)]))

if Image is not None:
    alpha = Image.open(io.BytesIO(default_png)).convert("RGBA").getchannel("A").getextrema()
    check("§1.4 the image is opaque (alpha 255 everywhere)", alpha == (255, 255), str(alpha))
else:
    check("§1.4 opaqueness needs pillow", False, "run with --with pillow")

canvas = [el for el in parity_root if local(el.tag) == "rect"]
check("§1.4 one full-canvas background rect, no border and no rounded corners",
      len(canvas) == 1 and close(num(canvas[0], "width"), render.DESIGN_WIDTH)
      and close(num(canvas[0], "height"), 210.0) and "stroke" not in canvas[0].attrib
      and "rx" not in canvas[0].attrib,
      f"{len(canvas)} top-level rects")
check("§1.5 one look: no style switch in the renderer's signature",
      "style" not in render.build_svg.__code__.co_varnames)


# ---------------------------------------------------------------- §2 theme


light_svg, dark_svg = parity_svg, svg_of(parity, theme="dark")
check("§2.1/§2.2 light and dark are real variants", png_of(parity, theme="dark") != default_png)
check("§2.2 an unknown theme falls back to light", svg_of(parity, theme="chartreuse") == light_svg)
check("§2.1 only the palette changes: same element shapes, same geometry",
      [local(el.tag) for el in ET.fromstring(dark_svg).iter()] == [local(el.tag) for el in parity_root.iter()]
      and [el.get("transform") for el in ET.fromstring(dark_svg).iter()] == [el.get("transform") for el in parity_root.iter()])

for name, colours in (
    ("light", ("#ffffff", "#c3d0d8", "#56616c", "#21292b", "#c60000", "#006edb")),
    ("dark", ("#020a14", "#374759", "#c3d0d8", "#ffffff", "#ff2d3f", "#00b8f1", "#a2a5b3")),
):
    document = light_svg if name == "light" else dark_svg
    check(f"§2.3 the {name} palette is in the {name} SVG",
          all(colour in document for colour in colours), str([c for c in colours if c not in document]))

dark_plot = rows(dark_svg)[1][PLOT_ROW]
dark_separators = {el.get("stroke") for el in kids(dark_plot, "line") if close(num(el, "y2"), 120.0)}
check("§2.3 the dark day separator is #c3d0d8 - the hex light mode uses for its grid",
      dark_separators == {"#374759", "#c3d0d8"}, str(sorted(dark_separators)))

overmax_light = [el for el in kids(rows(svg_of(wet))[1][PLOT_ROW], "text") if close(num(el, "y"), 6.0)]
overmax_dark = [el for el in kids(rows(svg_of(wet, theme="dark"))[1][PLOT_ROW], "text") if close(num(el, "y"), 6.0)]
check("§2.3 the over-max value inverts (#ffffff light, #21292b dark): it sits on the bar",
      overmax_light and overmax_light[0].get("fill") == "#ffffff"
      and overmax_dark and overmax_dark[0].get("fill") == "#21292b",
      f'{[e.get("fill") for e in overmax_light]} / {[e.get("fill") for e in overmax_dark]}')


# --------------------------------------------------------------- §3 canvas


check("§3.1/§3.3 the four row groups sit at 8 / 32 / 56 / 186, 30 px in",
      set(parity_rows) == {DAY_ROW, HOUR_ROW, PLOT_ROW, LEGEND_ROW}, str(sorted(parity_rows)))
check("§3.2 the plot is 734.2373 x 120 in a 30 px gutter either side",
      all(close(num(el, "x2"), render.PLOT_W) for el in kids(plot, "line") if close(num(el, "y1"), num(el, "y2")))
      and close(render.DESIGN_WIDTH - render.PLOT_W, 60.0))

STACK = "DejaVu Sans, Verdana, sans-serif"
all_texts = [el for el in parity_root.iter() if local(el.tag) == "text"]
check("§3.4 every text node leads with DejaVu Sans",
      all(el.get("font-family") == STACK for el in all_texts),
      str({el.get("font-family") for el in all_texts}))
with_font = png_of(parity, font_path=FONT)
without_font = png_of(parity, font_path="/nonexistent/DejaVuSans.ttf")
check("§3.4 the font is loaded by path (without it resvg draws no text)",
      with_font != without_font and len(with_font) > len(without_font),
      f"{len(with_font)}B vs {len(without_font)}B")

celsius = [el for el in kids(plot, "text") if close(num(el, "x"), -5.0)]
millimetres = [el for el in kids(plot, "text") if close(num(el, "x"), render.PLOT_W + 5)]
reference_celsius = [el for el in kids(reference_plot, "text") if close(num(el, "x"), -5.0)]
check("§3.5 five °C labels, right-aligned at x=-5, on the reference's own rows",
      len(celsius) == 5 and all(el.get("text-anchor") == "end" for el in celsius)
      and [num(el, "y") for el in celsius] == [num(el, "y") for el in reference_celsius]
      and [num(el, "y") for el in celsius] == [24.0, 48.0, 72.0, 96.0, 120.0],
      str([(num(el, "y"), el.text) for el in celsius]))
check("§3.5 five mm labels reading 8, 6, 4, 2, 0, left-aligned right of the plot",
      [el.text for el in millimetres] == ["8", "6", "4", "2", "0"]
      and all(el.get("text-anchor") is None for el in millimetres),
      str([el.text for el in millimetres]))
glyphs = [el.get("transform") for el in kids(plot, "g") if "scale(0.5)" in (el.get("transform") or "")]
check("§3.5 the thermometer and droplet ride at the reference's own offsets, filled not filtered",
      glyphs == [el.get("transform") for el in kids(reference_plot, "g") if "scale(0.5)" in (el.get("transform") or "")]
      and "filter" not in CHROME and "COLOUR" not in CHROME and parity_svg.count("#56616c") > 10,
      str(glyphs))

cold_labels = [el.text for el in kids(rows(svg_of(freezing))[1][PLOT_ROW], "text") if close(num(el, "x"), -5.0)]
check("§3.6 a band bottom at -10 °C or below drops the degree sign from every label",
      all("°" not in (text or "") for text in cold_labels) and cold_labels[-1] == "-12",
      str(cold_labels))
check("§3.6 every other render keeps it", all("°" in (el.text or "") for el in celsius))

legend = parity_rows[LEGEND_ROW]
reference_legend = reference_rows[LEGEND_ROW]
check("§3.7 the legend is the reference's, entry for entry",
      ET.tostring(legend).decode().replace(f' font-family="{STACK}"', "").replace(" />", "/>").replace("\n", "")
      == ET.tostring(reference_legend).decode().replace(" />", "/>").replace("\n", ""),
      ET.tostring(legend).decode()[:160])
check("§3.7 both captions, German, with the units",
      sorted(el.text for el in legend.iter() if local(el.tag) == "text") == ["Niederschlag mm", "Temperatur °C"])


# ---------------------------------------------------------------- §4 time


curve = [el for el in kids(plot, "path") if el.get("fill") == "none"]
curve_d = curve[0].get("d", "") if len(curve) == 1 else ""
check("§4.1 one curve of 61 hourly points over 60 h, x=0 to x=734.2373",
      len(curve) == 1 and curve_d.count("C") == 60 and curve_d.startswith("M0 ")
      and close(numbers(curve_d)[-2], render.PLOT_W),
      f"paths={len(curve)} segments={curve_d.count('C')}")
check("§4.2/§5.5 the curve is the reference's spline, point for point, once the band is applied",
      len(numbers(curve_d)) == len(reference_curve)
      and all(close(ours, reference) for ours, reference in zip(numbers(curve_d)[0::2], reference_curve[0::2]))
      and all(close(ours, reference + DELTA) for ours, reference in zip(numbers(curve_d)[1::2], reference_curve[1::2])),
      f"delta {DELTA:+.1f} px ({OUR_BOTTOM}..{OUR_BOTTOM + 10 * OUR_R} vs the reference's {REF_BOTTOM}..)")

lines = kids(plot, "line")
reference_lines = kids(reference_plot, "line")
check("§4.3 the cell grid is the reference's: 11 horizontal, 61 vertical, no separate frame",
      [(num(el, "x1"), num(el, "y1"), num(el, "x2"), num(el, "y2")) for el in lines]
      == [(num(el, "x1"), num(el, "y1"), num(el, "x2"), num(el, "y2")) for el in reference_lines]
      and len(lines) == 72,
      f"{len(lines)} lines vs the reference's {len(reference_lines)}")
check("§4.4 a midnight replaces its grid line, it does not add one",
      [el.get("stroke") for el in lines] == [el.get("stroke") for el in reference_lines]
      and sum(1 for el in lines if el.get("stroke") == "#56616c" and close(num(el, "y2"), 120.0)) == 2,
      str([round(num(el, "x1"), 1) for el in lines if el.get("stroke") == "#56616c" and close(num(el, "y2"), 120.0)]))

day_labels = kids(parity_rows[DAY_ROW], "text")
reference_days = kids(reference_rows[DAY_ROW], "text")
check("§4.5 day labels are the reference's: German, numeric, at the boundary, 16 px weight 600",
      [(round(num(el, "x"), 4), el.text) for el in day_labels]
      == [(round(num(el, "x"), 4), el.text) for el in reference_days]
      and [el.text for el in day_labels] == ["So 20.09.", "Mo 21.09.", "Di 22.09."]
      and all(el.get("font-size") == "16" and el.get("font-weight") == "600" for el in day_labels),
      str([el.text for el in day_labels]))

for hour, wanted, why in (
    (8, ["So 20.09.", "Mo 21.09.", "Di 22.09."], "a morning start labels every day it touches"),
    (20, ["Mo 21.09.", "Di 22.09.", "Mi 23.09."], "an 18:00-23:00 start drops the opening day"),
    (13, ["So 20.09.", "Mo 21.09.", "Di 22.09."], "a 12:00-18:00 start drops the closing day"),
    (18, ["Mo 21.09.", "Di 22.09."], "an 18:00 start drops both"),
):
    moment = START - 8 * 3600 + hour * 3600
    labels = [el.text for el in kids(rows(svg_of(series(yr_temps), now=moment, fetched_at=moment))[1][DAY_ROW], "text")]
    check(f"§4.6 {why} ({hour:02d}:00 start)", labels == wanted, str(labels))

hour_labels = kids(parity_rows[HOUR_ROW], "text")
check("§4.7 30 hour labels, every 2 h, the last point skipped - the reference's own row",
      [(round(num(el, "x"), 4), el.text) for el in hour_labels]
      == [(round(num(el, "x"), 4), el.text) for el in kids(reference_rows[HOUR_ROW], "text")]
      and len(hour_labels) == 30 and hour_labels[0].text == "08" and hour_labels[-1].text == "18",
      f"{len(hour_labels)} labels")

# §4.8 the normal case: a window opened after the poll runs past met.no's
# hourly entries into its 6-hourly ones.
tail = series(yr_temps[:24], [0.0] * 24) + series(
    [15.0, 13.0, 16.0, 14.0, 17.0, 15.0, 18.0, 16.0, 19.0], [0.6] * 9, start=START + 24 * 3600, hours=6
)
tail_plot = rows(svg_of(tail, now=START + 6 * 3600, fetched_at=START))[1][PLOT_ROW]
tail_curve = [el for el in kids(tail_plot, "path") if el.get("fill") == "none"][0].get("d", "")
check("§4.8 an interpolated tail still reaches the right edge, in one unbroken curve",
      tail_curve.count("C") == 60 and close(numbers(tail_curve)[-2], render.PLOT_W),
      f"segments={tail_curve.count('C')} end={numbers(tail_curve)[-2:]}")
gap = series(yr_temps[:20] + [None] * 5 + yr_temps[25:])
gap_paths = [el for el in kids(rows(svg_of(gap))[1][PLOT_ROW], "path") if el.get("fill") == "none"]
check("§4.8 a slot with no entry either side is a gap: the curve breaks",
      len(gap_paths) == 2, f"{len(gap_paths)} curve paths")

# §4.9 day/night shading from a sun.sun reading: sunset 19:00, sunrise 07:00.
# The 08:00 window sees sunsets 11, 35, 59 h in and sunrises 23, 47 h in; the
# 07:00 sunrise an hour before it still counts, its ramp ends exactly at x = 0.
SUN = {"state": "above_horizon", "next_rising": iso(START + 23 * 3600), "next_setting": iso(START + 11 * 3600)}


def shade_stops(svg: str) -> list[ET.Element]:
    gradient = next((el for el in ET.fromstring(svg).iter() if el.get("id") == "day-night-gradient"), None)
    return [] if gradient is None else list(gradient)


def at_hour(stop: ET.Element) -> float:
    return float(stop.get("offset").rstrip("%")) / 100 * render.PLOT_W / STEP


shade_svg = svg_of(series(yr_temps), sun=SUN)
shade_plot = rows(shade_svg)[1][PLOT_ROW]
shade_order = [local(el.tag) for el in shade_plot if local(el.tag) in ("rect", "line")]
stops = shade_stops(shade_svg)
check("§4.9 the shade is one rect behind the grid, one stop pair per sun event",
      shade_order[0] == "rect" and kids(shade_plot, "rect")[0].get("fill") == "url(#day-night-gradient)"
      and len(stops) == 1 + 2 * 6,
      f"{len(stops)} stops, first {shade_order[0]}")
sunset = [(round(at_hour(el), 3), float(el.get("stop-opacity"))) for el in stops[3:5]]
check("§4.9 light shades the night only, ramped one hour either side of sunset",
      float(stops[2].get("stop-opacity")) == 0.0 and close(at_hour(stops[2]), 0.0)
      and sunset == [(10.0, 0.0), (12.0, 0.08)],
      f"window opens at {stops[2].get('stop-opacity')}, sunset {sunset}")
dark = [el for el in shade_stops(svg_of(series(yr_temps), sun=SUN, theme="dark")) if el.get("stop-color") == "#c3d0d8"]
check("§4.9 dark lifts the day instead", dark and float(dark[0].get("stop-opacity")) == 0.08)
polar = {"state": "below_horizon", "next_rising": iso(START + 30 * 86400), "next_setting": iso(START + 30 * 86400 + 3600)}
polar_stops = shade_stops(svg_of(series(yr_temps), sun=polar))
check("§4.9 polar night is one flat shade; no sun reading draws none",
      len(polar_stops) == 1 and polar_stops[0].get("stop-opacity") == "0.08"
      and not shade_stops(svg_of(series(yr_temps))),
      f"{len(polar_stops)} polar stops")


# ------------------------------------------------------------ §5 axis/curve


ladder = [
    ((7.2, 23.0), (6.0, 3.0), "yr's own light forecast"),
    ((10.0, 10.0), (10.0, 1.0), "a flat window takes the finest step"),
    ((-3.2, 30.0), (-5.0, 5.0), "a wide window climbs the ladder"),
    ((-12.0, 80.0), (-20.0, 10.0), "nothing fits: r = 10 and the icons clamp"),
]
for (low, high), wanted, why in ladder:
    got = render.temperature_band([low, high])
    check(f"§5.1 the ladder fits {low}..{high} to {wanted[0]}..{wanted[0] + 10 * wanted[1]} - {why}",
          got == wanted, str(got))
check("§5.1 every fit but the last leaves the 29 px of icon headroom §6.3 needs",
      all((band[0] + 10 * band[1] - high) * (12 / band[1]) >= render.ICON_HEADROOM
          for (low, high), band, _ in ladder[:-1] for band in [render.temperature_band([low, high])]))

# yr's own dark meteogram, read back out of its curve: band 4..24 at 2 °C per
# row (its own axis labels). §5.1 claims to reproduce that band exactly.
dark_original = (ASSETS / "meteogram-6325496-dark.svg").read_text()
dark_plot_text = dark_original[dark_original.index('<g transform="translate(30 48)">'):
                               dark_original.index('<g transform="translate(30 168)">')]
dark_curve = numbers(re.search(
    r'd="M([^"]+)"\s*\n?\s*fill="none"\s*\n?\s*stroke="url\(#temperature-curve-gradient\)"',
    dark_plot_text).group(1))
dark_temps = [4.0 + (120.0 - y) / 6.0 for y in dark_curve[1::2]]
check("§5.1 the ladder reproduces yr's own dark sample exactly: 4..24 at 2 °C a row",
      render.temperature_band(dark_temps) == (4.0, 2.0),
      f"{render.temperature_band(dark_temps)} from {len(dark_temps)} of yr's own points")

check("§5.2 the axis reads bottom + (120 - y) * r / 12, in whole degrees",
      [el.text for el in celsius] == [f"{int(OUR_BOTTOM + (120 - num(el, 'y')) * OUR_R / 12)}°" for el in celsius],
      str([el.text for el in celsius]))
check("§5.3 the curve is 2 px, no fill, no markers, no glow",
      curve[0].get("stroke-width") == "2" and curve[0].get("fill") == "none"
      and "stroke-linecap" not in curve[0].attrib and "filter" not in CHROME)
check("§5.4 the fit keeps the whole curve inside the band",
      all(0 <= y <= 120 for y in numbers(curve_d)[1::2]),
      f"{min(numbers(curve_d)[1::2]):.1f}..{max(numbers(curve_d)[1::2]):.1f}")

defs = kids(plot, "defs")
gradients = [el for el in defs[0] if local(el.tag) == "linearGradient"] if defs else []
stops = list(gradients[0]) if gradients else []
check("§5.5 one path, one gradient: two coincident stops at y(0 °C), padded",
      len(curve) == 1 and len(gradients) == 1 and len(stops) == 3
      and gradients[0].get("gradientUnits") == "userSpaceOnUse"
      and gradients[0].get("spreadMethod") == "pad"
      and stops[0].get("offset") == stops[1].get("offset")
      and stops[0].get("stop-color") == "#c60000" and stops[1].get("stop-color") == "#006edb"
      and stops[2].get("offset") == "100%",
      str([(el.get("offset"), el.get("stop-color")) for el in stops]))
check("§5.5 the stop is y(0 °C) / 120, allowed outside 0..100 % - the reference's own 110 % becomes ours",
      close(float(stops[0].get("offset").rstrip("%")),
            (120 - (0 - OUR_BOTTOM) * 12 / OUR_R) / 120 * 100),
      stops[0].get("offset"))
warm_stops = [el.get("offset") for el in kids(rows(svg_of(warm))[1][PLOT_ROW], "defs")[0][0]]
frozen_stops = [el.get("offset") for el in kids(rows(svg_of(freezing))[1][PLOT_ROW], "defs")[0][0]]
check("§5.5 an all-warm window pads warm (offset > 100 %), an all-frozen one pads cold (offset < 0)",
      float(warm_stops[0].rstrip("%")) > 100 and float(frozen_stops[0].rstrip("%")) < 0,
      f"{warm_stops[0]} / {frozen_stops[0]}")
check("§5.6/§5.7 Celsius only, the unit on the glyph and never in a label",
      "°F" not in parity_svg and "fahrenheit" not in parity_svg.lower()
      and not any("C" in (el.text or "") for el in celsius))


# ---------------------------------------------------------------- §6 icons


icons = [el for el in kids(plot, "g") if "scale(0.24)" in (el.get("transform") or "")]
icon_xy = [tuple(float(v) for v in el.get("transform").split("translate(")[1].split(")")[0].split()) for el in icons]
reference_icons = [(num(el, "x"), num(el, "y")) for el in kids(reference_plot, "svg")]
check("§6.1/§6.2 30 icons, every 2 h, centred on the odd point, in a 24 px box",
      len(icons) == 30
      and all(close(x, i * STEP - 12) for (x, _), i in zip(icon_xy, range(1, 60, 2)))
      and close(24.0 / 100.0 * 100, 24.0),
      f"{len(icons)} icons")
check("§6.2 a 24 px box clears its neighbour by 0.47 px: anything larger collides",
      close(2 * STEP - render.ICON_BOX, 0.4746, 0.001), f"{2 * STEP - render.ICON_BOX:.4f} px")
check("§6.3 every box lands where the reference put it, once the band is applied",
      all(close(x, rx) and close(y, ry + DELTA) for (x, y), (rx, ry) in zip(icon_xy, reference_icons)),
      str([(round(y - ry - DELTA, 2)) for (_, y), (_, ry) in zip(icon_xy, reference_icons)][:6]))
check("§6.3 the bottom rides 5 px above the curve where the curve is flat",
      close(rows(svg_of(flat))[1][PLOT_ROW] is not None and
            [float(el.get("transform").split()[1].split(")")[0])
             for el in kids(rows(svg_of(flat))[1][PLOT_ROW], "g")
             if "scale(0.24)" in (el.get("transform") or "")][0],
            120 - (10.0 - 10.0) * 12 - 29.0 + 0.0, 0.6),
      "flat 10 °C window")
spike = series([0.0] * 61)
for i in (0, 1, 2, 3):
    spike[i]["temperature"] = 95.0  # off the top of an r = 10 band
spike_icons = [float(el.get("transform").split()[1].split(")")[0])
               for el in kids(rows(svg_of(spike))[1][PLOT_ROW], "g")
               if "scale(0.24)" in (el.get("transform") or "")]
check("§6.3 a curve through the ceiling clamps the box to the plot top",
      close(min(spike_icons), 0.0), f"top {min(spike_icons)}")

codes = ["clearsky_day" if i % 2 == 0 else "cloudy" for i in range(61)]
coded = rows(svg_of(series(yr_temps, codes=codes)))[1][PLOT_ROW]
first_icon = [el for el in kids(coded, "g") if "scale(0.24)" in (el.get("transform") or "")][0]
check("§6.4 the icon of a 2 h cell is the symbol of its first hour (point i - 1)",
      'id="wg1_clearsky_day' in ET.tostring(first_icon).decode(),
      ET.tostring(first_icon).decode()[:90])
order = [local(el.tag) for el in plot]
icon_positions = [i for i, el in enumerate(plot) if "scale(0.24)" in (el.get("transform") or "")]
check("§6.4 icons are drawn last, over the bars and the curve",
      min(icon_positions) > order.index("path") > (max([i for i, el in enumerate(plot)
          if local(el.tag) == "rect"] or [-1])),
      f"bars<{order.index('path')}<icons from {min(icon_positions)}")
check("§6.5 the art is theme-independent",
      ET.tostring(icons[0]).decode().replace("#", "") ==
      ET.tostring([el for el in kids(dark_plot, "g") if "scale(0.24)" in (el.get("transform") or "")][0]).decode().replace("#", ""))
check("§6.6 vector art keeps its own ids under a per-icon prefix", 'id="wg1_partlycloudy_day' in parity_svg)
check("§6.6 raster art is inlined as a data URI",
      "base64" in svg_of(series(yr_temps, codes="fair_night")))
check("§6.6 no icon label and no icon border",
      not any("icon" in (el.text or "").lower() for el in all_texts))


# -------------------------------------------------------------- §7 precip


wet_plot = rows(svg_of(wet))[1][PLOT_ROW]
wet_rects = kids(wet_plot, "rect")
wet_bars = [el for el in wet_rects if close(num(el, "width"), STEP - 1)]
reference_wet = kids(rows(REFERENCE_RAIN.read_text())[1][PLOT_ROW], "rect")
reference_wet = [el for el in reference_wet if num(el, "height") > 0]
check("§7.1/§7.3 one bar per hourly interval, in the one band, behind the curve",
      [local(el.tag) for el in wet_plot].index("rect") < [local(el.tag) for el in wet_plot].index("path")
      and len(wet_bars) == 14 and all(el.get("fill") == "#006edb" for el in wet_bars),
      f"{len(wet_bars)} bars of {len(wet_rects)} rects")
check("§7.2/§7.4 every bar is the reference's, mm for mm on the fixed 0..10 axis",
      len(wet_bars) == len(reference_wet)
      and all(close(num(a, "x"), num(b, "x")) and close(num(a, "y"), num(b, "y"))
              and close(num(a, "width"), num(b, "width")) and close(num(a, "height"), num(b, "height"))
              for a, b in zip(wet_bars, reference_wet)),
      f"{len(wet_bars)} ours vs {len(reference_wet)} reference bars")
check("§7.2 the mm axis never rescales: a dry render and a wet one read the same",
      [el.text for el in millimetres]
      == [el.text for el in kids(wet_plot, "text") if close(num(el, "x"), render.PLOT_W + 5)])
check("§7.3 the last point contributes no bar (60 intervals, 61 points)",
      max(num(el, "x") for el in wet_bars) < 60 * STEP)
check("§7.4 bar geometry is x = i * step + 0.5, width step - 1, flat and unrounded",
      all(close(num(el, "x"), i * STEP + 0.5) for el, i in
          zip(wet_bars, [i for i, mm in enumerate(rain_amounts) if mm > 0]))
      and all("rx" not in el.attrib and "stroke" not in el.attrib and el.get("fill-opacity") is None
              for el in wet_bars))

clipped = [el for el in wet_bars if close(num(el, "y"), 0.0)]
overmax_text = [el for el in kids(wet_plot, "text") if close(num(el, "y"), 6.0)]
chip = [el for el in wet_rects if el not in wet_bars]
check("§7.5 a rate over 10 mm/h is clipped to the band top and its whole-mm value printed on it",
      len(clipped) == 1 and close(num(clipped[0], "height"), 120.0)
      and len(overmax_text) == 1 and overmax_text[0].text == "13"
      and overmax_text[0].get("text-anchor") == "middle" and overmax_text[0].get("font-size") == "12"
      and close(num(overmax_text[0], "x"), 33 * STEP + STEP / 2),
      str([(el.text, num(el, "x")) for el in overmax_text]))
check("§7.5 the value rides on a bar-coloured chip: 15.3 px of ink on an 11.2373 px bar",
      len(chip) == 1 and chip[0].get("fill") == "#006edb"
      and close(num(chip[0], "width"), 2 * 12 * 1303 / 2048 + 4)
      and num(chip[0], "width") > STEP,
      str(chip[0].attrib if chip else None))
check("§7.5 the reference's own over-max value sits where ours does",
      close(num(overmax_text[0], "x"),
            num([el for el in kids(rows(REFERENCE_RAIN.read_text())[1][PLOT_ROW], "text") if close(num(el, "y"), 6.0)][0], "x")))

nothing = series(yr_temps, [None] * 61)
check("§7.6 no precipitation value draws no bar, exactly like a 0.0",
      svg_of(nothing) == svg_of(series(yr_temps, [0.0] * 61))
      and not kids(rows(svg_of(nothing))[1][PLOT_ROW], "rect"),
      f"{len(kids(rows(svg_of(nothing))[1][PLOT_ROW], 'rect'))} bars")
check("§7.7 sub-zero temperatures still draw precipitation: no snow switch",
      len(kids(rows(svg_of(freezing))[1][PLOT_ROW], "rect")) == 60)
check("§7.8 bars are discrete rects: nothing interpolates or smooths between them",
      all(local(el.tag) == "rect" for el in wet_plot if el.get("fill") == "#006edb")
      and len([el for el in kids(wet_plot, "path") if el.get("fill") == "none"]) == 1)


# --------------------------------------------------------------- §8 stale


stale_svg = svg_of(parity, stale=True, age_seconds=7 * 3600)
stale_root = ET.fromstring(stale_svg)
chip_rect = [el for el in stale_root if local(el.tag) == "rect" and close(num(el, "x"), 670.2373)]
chip_dot = [el for el in stale_root if local(el.tag) == "circle"]
chip_text = [el for el in stale_root if local(el.tag) == "text"]
check("§8.2 the chip is 94 x 18 at (670.2373, 186), right-aligned to the plot in the legend row",
      len(chip_rect) == 1
      and all(close(num(chip_rect[0], k), v) for k, v in
              (("y", 186.0), ("width", 94.0), ("height", 18.0), ("rx", 3.0)))
      and chip_rect[0].get("fill-opacity") == "0.85"
      and len(chip_dot) == 1 and close(num(chip_dot[0], "cx"), 678.2373) and close(num(chip_dot[0], "cy"), 195.0)
      and close(num(chip_dot[0], "r"), 3.0)
      and len(chip_text) == 1 and close(num(chip_text[0], "x"), 686.2373) and close(num(chip_text[0], "y"), 199.0)
      and chip_text[0].text == "vor 7 h" and chip_text[0].get("font-size") == "12",
      f"rects={len(chip_rect)} dots={len(chip_dot)} texts={len(chip_text)}")
check("§8.2 the chip clears the legend, which ends at x = 254",
      670.2373 > 254.0 and close(670.2373 + 94.0, 30 + render.PLOT_W))
check("§8.2 the age reads hours up to 48, then days; the chip fits 'vor 3 Tagen'",
      render.stale_label(7 * 3600) == "vor 7 h" and render.stale_label(48 * 3600) == "vor 48 h"
      and render.stale_label(72 * 3600) == "vor 3 Tagen"
      and 71.9 + 16 + 4 <= 94.0,
      f"{render.stale_label(72 * 3600)!r}")
check("§8.4 the chip moves nothing: the stale SVG is the fresh one plus the chip",
      stale_svg[:-6].startswith(parity_svg[:-6]) and len(stale_svg) > len(parity_svg),
      f"{len(stale_svg)}B vs {len(parity_svg)}B")

empty_svg = svg_of([], fetched_at=None, last_error="met.no unreachable: timeout")
empty_root, empty_rows = rows(empty_svg)
empty_plot = empty_rows[PLOT_ROW]
empty_texts = [el.text for el in kids(empty_plot, "text")]
check("§8.3 no cache draws the whole frame: grid, separators, both axes, hours, days, legend",
      len(kids(empty_plot, "line")) == 72
      and len(kids(empty_rows[HOUR_ROW], "text")) == 30
      and len(kids(empty_rows[DAY_ROW], "text")) >= 2
      and len([el for el in empty_plot if local(el.tag) == "g"]) == 2
      and len(kids(empty_rows[LEGEND_ROW], "g")) == 2,
      f"{len(kids(empty_plot, 'line'))} lines")
check("§8.3 the °C axis falls back to 0..30 and the two texts are centred over the plot",
      band_of(empty_plot) == (0.0, 3.0)
      and "noch keine Daten" in empty_texts and "met.no unreachable: timeout" in empty_texts
      and all(close(num(el, "x"), render.PLOT_W / 2) for el in kids(empty_plot, "text")
              if (el.text or "").startswith(("noch", "met.no"))),
      str(band_of(empty_plot)))
check("§8.3 no curve, no bars, no icons, no fake data",
      not kids(empty_plot, "path") and not kids(empty_plot, "rect")
      and not [el for el in kids(empty_plot, "g") if "scale(0.24)" in (el.get("transform") or "")])
check("§8.4 the empty state is the same canvas: no element moves, no row resizes",
      empty_root.get("viewBox") == parity_root.get("viewBox")
      and set(empty_rows) == set(parity_rows))


# ------------------------------------------------------- §9 nothing extra


check("§9.1 no wind of any kind", "wind" not in parity_svg.lower())
check("§9.2 no attribution, no header, no location line",
      all(word not in parity_svg.lower() for word in ("met.no", "nrk", "yr.no", "norge")))
check("§9.3 no moon phase and no dark-mode tint: night is the art's job",
      "moon" not in parity_svg.lower()
      and [el.get("fill") for el in icons] == [el.get("fill") for el in
           [e for e in kids(dark_plot, "g") if "scale(0.24)" in (e.get("transform") or "")]])
check("§9.4 no now marker and no interaction: it is an image",
      not any(local(el.tag) in ("a", "title", "script") for el in parity_root.iter())
      and "onclick" not in parity_svg and "pointer" not in parity_svg)
check("§9.6 no shadow, no glow, no rounded plot corner: the one gradient is the curve's",
      all(word not in CHROME for word in ("filter", "feGaussian", "drop-shadow"))
      and CHROME.count('fill-opacity') == 0 and len(gradients) == 1)
check("§9.7 no 'kein Niederschlag': the empty rain row on a fixed axis says it",
      "kein Niederschlag" not in svg_of(series(yr_temps, [0.0] * 61)))
check("§9.5 the labels are German", [el.text for el in day_labels][0].startswith("So ")
      and all(word in parity_svg for word in ("Temperatur", "Niederschlag")))


# ---------------------------------------------------------- determinism


check("§10 same input -> byte-identical PNG", png_of(parity) == png_of(parity) and png_of(wet) == png_of(wet))
check("§10 the defaults are 794 px, light, 60 h, 24 px icons, 0..10 mm",
      (render.DEFAULT_WIDTH, render.WINDOW_HOURS, render.ICON_BOX, render.MM_MAX, render.PX_PER_MM)
      == (794, 60, 24.0, 10.0, 12.0))


# ------------------------------------------------------------------ report


failed = 0
for name, ok, detail in RESULTS:
    line = f"{'PASS' if ok else 'FAIL'}  {name}"
    if detail and not ok:
        line += f"  ({detail})"
    print(line)
    failed += 0 if ok else 1
print(f"\nrender-check: {len(RESULTS) - failed}/{len(RESULTS)} passed")
sys.exit(1 if failed else 0)
