"""Build the wind-stripped layout reference (ticket 01).

Every geometric constant below is lifted from
`tools/assets/meteogram-6325496.svg` - yr's own light meteogram - not redrawn
by eye. The only changes: the wind band and the yr/NRK header are gone, the
window is 60 h / 61 points instead of 59 h / 60, and the labels are German.

Usage:  python3 tools/make-reference-v2.py
Writes: tools/assets/graph-reference-v2.svg      (yr's own dry forecast)
        tools/assets/graph-reference-v2-rain.svg (same geometry, rain sample incl. an over-max bar)
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
ORIGINAL = ASSETS / "meteogram-6325496.svg"

# --- cloned constants -------------------------------------------------------
STEP = 722 / 59                 # 12.2373 px per hour. yr's plot is 722 px wide over 59 intervals.
HOURS = 60                      # our window: 60 h, 61 points (yr's file happened to carry 59 h)
PLOT_W = HOURS * STEP           # 734.2373
PLOT_H = 120                    # unchanged
GUTTER_L = 30                   # yr's plot group sits at x=30
GUTTER_R = 30                   # 782 - 30 - 722
PX_PER_C = 4                    # 24 px per 6 degC
PX_PER_MM = 12                  # 24 px per 2 mm, 0..10 mm over 120 px
GRID_DY = 12                    # horizontal grid every 12 px (3 degC / 1 mm)
LABEL_DY = 24                   # axis labels every second grid line
BAR_INSET = 0.5                 # rain bar: x = i*STEP + 0.5, width = STEP - 1
CURVE_STROKE = 2
TEMP_BOTTOM_LABEL = 3           # yr's ladder here: 27/21/15/9/3 deg, so y=120 is 3 degC

FONT = "DejaVu Sans, Verdana, sans-serif"
F_DAY, F_SMALL, F_OVERMAX = 16, 13, 12      # yr's rem sizes at a 15 px root

C_BG = "#ffffff"
C_GRID = "#c3d0d8"
C_SEP = "#56616c"
C_TEXT = "#21292b"
C_MUTED = "#56616c"
C_WARM = "#c60000"
C_COLD = "#006edb"
C_RAIN = "#006edb"
C_OVERMAX = "#ffffff"

# --- rows (the header's 84.86 px are gone; the inner offsets are yr's) ------
TOP = 8                         # replaces yr's header block
ROW_DAY = TOP                   # day labels, 24 tall
ROW_HOUR = TOP + 24             # hour labels, 24 tall
ROW_PLOT = TOP + 48             # the one band
ROW_LEGEND = ROW_PLOT + PLOT_H + 10   # yr's gap between the last band and the legend
CANVAS_W = GUTTER_L + PLOT_W + GUTTER_R
CANVAS_H = ROW_LEGEND + 18 + 6

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

THERMO = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="none" viewBox="0 0 24 24">'
          '<circle cx="12" cy="18" r="1.25" stroke="COLOUR" stroke-width="1.5"/>'
          '<path stroke="COLOUR" stroke-width="1.5" d="M12 17V8m0-5a3 3 0 0 0-3 3v9.354a4 4 0 1 0 6 0V6a3 3 0 0 0-3-3z"/></svg>')


def n(v):
    return f"{v:.4f}".rstrip("0").rstrip(".")


def original_art():
    """yr's own 24 px symbol art and droplet glyph, lifted from the original file."""
    s = ORIGINAL.read_text()
    i = s.index('<g transform="translate(30 48)">')
    j = s.index('<g transform="translate(30 168)">')
    plot = s[i:j]
    blocks = re.findall(r'<svg\s+x="[\d.]+"\s+y="[\d.]+"\s+width="24"\s+height="24"\s*>\s*(<svg xmlns.*?</svg>)\s*</svg>', plot, re.S)
    codes = [re.search(r'id="(\w+?)__', b).group(1) for b in blocks]
    art = {}
    for c, b in zip(codes, blocks):
        art.setdefault(c, b)
    droplet = re.search(r'<g transform="translate\(727, -6\) scale\(0.5\)"[^>]*>\s*(<svg[^>]*>.*?</svg>)', plot, re.S).group(1)
    return codes, art, droplet.replace("currentColor", C_MUTED)


def original_series():
    """yr's own hourly temperatures, read back out of its curve path."""
    s = ORIGINAL.read_text()
    i = s.index('<g transform="translate(30 48)">')
    j = s.index('<g transform="translate(30 168)">')
    d = re.search(r'<path\s+d="M([^"]+)"\s+fill="none"\s+stroke="url\(#temperature-curve-gradient\)"', s[i:j]).group(1)
    segs = d.split("C")
    ys = [float(segs[0].split()[1])]
    for seg in segs[1:]:
        ys.append([float(x) for x in re.findall(r"-?\d+\.?\d*", seg)][5])
    return [TEMP_BOTTOM_LABEL + (PLOT_H - y) / PX_PER_C for y in ys]


def catmull_rom(points):
    """yr's smoothing: Catmull-Rom to cubic Bezier, tension 1/6, ends duplicated."""
    out = [f"M{n(points[0][0])} {n(points[0][1])}"]
    for k in range(len(points) - 1):
        p0 = points[k - 1] if k > 0 else points[0]
        p1, p2 = points[k], points[k + 1]
        p3 = points[k + 2] if k + 2 < len(points) else points[-1]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        out.append(f"C{n(c1[0])} {n(c1[1])}, {n(c2[0])} {n(c2[1])}, {n(p2[0])} {n(p2[1])}")
    return "".join(out)


def bezier_y(points, x):
    """Curve height at x, sampled off the same spline the path draws."""
    k = max(0, min(int(x / STEP), len(points) - 2))
    p0 = points[k - 1] if k > 0 else points[0]
    p1, p2 = points[k], points[k + 1]
    p3 = points[k + 2] if k + 2 < len(points) else points[-1]
    c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
    c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
    best = None
    for q in range(101):
        t = q / 100
        u = 1 - t
        xx = u**3 * p1[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t**3 * p2[0]
        yy = u**3 * p1[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t**3 * p2[1]
        if best is None or abs(xx - x) < best[0]:
            best = (abs(xx - x), yy)
    return best[1]


def build(temps, rain, start_hour=8, start_weekday=6, start_day=20, start_month=9):
    codes, art, droplet = original_art()
    top_c = TEMP_BOTTOM_LABEL + PLOT_H / PX_PER_C          # degC at y=0
    y_temp = lambda t: PLOT_H - (t - TEMP_BOTTOM_LABEL) * PX_PER_C
    pts = [(i * STEP, y_temp(t)) for i, t in enumerate(temps)]

    o = []
    o.append(f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
             f'width="{n(CANVAS_W)}" height="{n(CANVAS_H)}" style="background-color:{C_BG}">')
    o.append(f'<style>text {{ font-family: {FONT}; }}</style>')
    o.append(f'<rect x="0" y="0" width="{n(CANVAS_W)}" height="{n(CANVAS_H)}" fill="{C_BG}" />')

    # --- day labels (yr: x = the day boundary, anchored start) --------------
    o.append(f'<g transform="translate({GUTTER_L} {ROW_DAY})">')
    hour = start_hour
    wd, day, month = start_weekday, start_day, start_month
    boundaries = [(0, wd, day, month)]
    for i in range(1, HOURS + 1):
        hour = (hour + 1) % 24
        if hour == 0:
            wd = (wd + 1) % 7
            day += 1
            boundaries.append((i, wd, day, month))
    for i, w, d, m in boundaries:
        o.append(f'<text x="{n(i * STEP)}" y="12" dy="0.35em" font-size="{F_DAY}" font-weight="600" '
                 f'fill="{C_TEXT}">{WEEKDAYS[w]} {d:02d}.{m:02d}.</text>')
    o.append("</g>")

    # --- hour labels, every 2 h; the final point is skipped, its label would
    #     sit on the mm axis (yr's own window ends on an odd index and dodges it)
    o.append(f'<g transform="translate({GUTTER_L} {ROW_HOUR})">')
    for i in range(0, HOURS, 2):
        o.append(f'<text x="{n(i * STEP)}" y="9" dy="0.35em" text-anchor="middle" font-size="{F_SMALL}" '
                 f'fill="{C_MUTED}">{(start_hour + i) % 24:02d}</text>')
    o.append("</g>")

    # --- the one band -------------------------------------------------------
    o.append(f'<g transform="translate({GUTTER_L} {ROW_PLOT})">')
    for k in range(PLOT_H // GRID_DY + 1):
        y = k * GRID_DY
        o.append(f'<line x1="0" x2="{n(PLOT_W)}" y1="{y}" y2="{y}" stroke="{C_GRID}" />')
    midnights = {i for i, *_ in boundaries[1:]}
    for i in range(HOURS + 1):
        x = n(i * STEP)
        colour = C_SEP if i in midnights else C_GRID
        o.append(f'<line x1="{x}" x2="{x}" y1="0" y2="{PLOT_H}" stroke="{colour}" />')

    # axis glyphs (yr recolours these with a CSS filter; we set the fill directly)
    o.append(f'<g transform="translate(-17, -6) scale(0.5)">{THERMO.replace("COLOUR", C_MUTED)}</g>')
    o.append(f'<g transform="translate({n(PLOT_W + 5)}, -6) scale(0.5)">{droplet}</g>')
    for k in range(1, PLOT_H // LABEL_DY + 1):
        y = k * LABEL_DY
        o.append(f'<text x="-5" y="{n(y)}" dy="0.35em" text-anchor="end" font-size="{F_SMALL}" '
                 f'fill="{C_MUTED}">{n(top_c - y / PX_PER_C)}°</text>')
        o.append(f'<text x="{n(PLOT_W + 5)}" y="{n(y)}" dy="0.35em" font-size="{F_SMALL}" '
                 f'fill="{C_MUTED}">{n((PLOT_H - y) / PX_PER_MM)}</text>')

    # rain bars, then the curve on top
    overmax = []
    for i, mm in enumerate(rain):
        h = min(mm * PX_PER_MM, PLOT_H)
        o.append(f'<rect x="{n(i * STEP + BAR_INSET)}" y="{n(PLOT_H - h)}" width="{n(STEP - 2 * BAR_INSET)}" '
                 f'height="{n(h)}" fill="{C_RAIN}" />')
        if mm * PX_PER_MM > PLOT_H:
            overmax.append((i, mm))
    o.append(f'<defs><linearGradient id="temperature-curve-gradient" x1="0" y1="0" x2="0" y2="{PLOT_H}" '
             f'gradientUnits="userSpaceOnUse" spreadMethod="pad">')
    zero = y_temp(0) / PLOT_H * 100
    o.append(f'<stop offset="{n(zero)}%" stop-color="{C_WARM}" /><stop offset="{n(zero)}%" stop-color="{C_COLD}" />'
             f'<stop offset="100%" stop-color="{C_COLD}" /></linearGradient></defs>')
    o.append(f'<path d="{catmull_rom(pts)}" fill="none" stroke="url(#temperature-curve-gradient)" '
             f'stroke-width="{CURVE_STROKE}" />')
    for i, mm in overmax:      # printed on the clipped bar, so it takes the contrasting fill.
        # Rounded to whole mm: at 12.24 px per column nothing longer fits inside the bar,
        # and the white fill is only legible where it sits on blue.
        o.append(f'<text x="{n(i * STEP + STEP / 2)}" y="6" dy="0.35em" text-anchor="middle" '
                 f'font-size="{F_OVERMAX}" fill="{C_OVERMAX}">{round(mm)}</text>')

    # symbols: 24 px, every 2 h, centred on the odd point (mid-cell), riding
    # above the curve's highest point across their own width
    for k, i in enumerate(range(1, HOURS, 2)):
        x = i * STEP - 12
        span = min(bezier_y(pts, min(max(x + q, 0), PLOT_W)) for q in range(25))
        y = max(0.0, span - 24 - 5)
        code = codes[k % len(codes)]
        o.append(f'<svg x="{n(x)}" y="{n(y)}" width="24" height="24">{art[code]}</svg>')
    o.append("</g>")

    # --- legend (yr's entry offsets, minus the wind entry) ------------------
    o.append(f'<g transform="translate({GUTTER_L}, {n(ROW_LEGEND)})">')
    o.append(f'<g transform="translate(0, 0)"><svg x="0" y="4" width="10" height="10">'
             f'<rect x="0" y="40%" width="100%" height="20%" fill="{C_WARM}" /></svg>'
             f'<text x="14" y="9" dy="0.35em" font-size="{F_SMALL}" fill="{C_MUTED}">Temperatur °C</text></g>')
    o.append(f'<g transform="translate(126, 0)"><svg x="0" y="4" width="10" height="10">'
             f'<rect x="0" y="0" width="100%" height="100%" fill="{C_RAIN}" /></svg>'
             f'<text x="14" y="9" dy="0.35em" font-size="{F_SMALL}" fill="{C_MUTED}">Niederschlag mm</text></g>')
    o.append("</g>")
    o.append("</svg>")
    return "\n".join(o)


def main():
    temps = original_series()
    temps.append(temps[-1] + (temps[-1] - temps[-2]))     # 60 points -> 61
    dry = [0.0] * 58 + [0.1] + [0.0]                      # yr's own: a single 0.1 mm hour
    (ASSETS / "graph-reference-v2.svg").write_text(build(temps, dry))

    wet = [0.0] * (HOURS)
    for i, mm in [(6, 0.3), (7, 1.1), (8, 2.4), (9, 1.6), (10, 0.4), (23, 0.2), (24, 0.9),
                  (31, 4.2), (32, 8.6), (33, 13.4), (34, 5.1), (35, 1.3), (48, 0.6), (49, 0.2)]:
        wet[i] = mm
    (ASSETS / "graph-reference-v2-rain.svg").write_text(build(temps, wet))
    print("wrote tools/assets/graph-reference-v2.svg, tools/assets/graph-reference-v2-rain.svg")
    print(f"canvas {n(CANVAS_W)} x {n(CANVAS_H)}  plot {n(PLOT_W)} x {PLOT_H}  step {STEP!r}")


if __name__ == "__main__":
    main()
