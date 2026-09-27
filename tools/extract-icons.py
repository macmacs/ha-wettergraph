#!/usr/bin/env python3
"""Build the graph widget's weather icon set under `wettergraph/app/icons/`.

Sources, in priority order:

1. The 7 icon families inside `tools/assets/meteogram-6325496.svg` (yr's own
   vector art, keyed by MET's legacy numeric symbol id).
2. The vendored `wettergraph/app/source-webp/weather_icon_*.webp` files for the
   remaining symbol codes. They are the same MET art (a copy of the Android
   app's metno/weathericons set, kept here so this repo stays self-contained),
   just raster, so a mixed set stays visually consistent.

Output per symbol code: an SVG with `viewBox="0 0 100 100"` whose art hangs on
one stable handle, `<g id="<symbol_code>">`. Plus `icons/index.json` mapping
every symbol_code to its file, and `tools/assets/icon-contact-sheet.png`, one
contact sheet showing all icons at 92 px, at the retired 28 px, and at the 24 px
the graph uses now (graph-spec §6.2) over each theme's background and cell grid.

Usage (the script needs resvg-py for the render checks):

    uv run --with resvg-py python tools/extract-icons.py [--font /path/to/DejaVuSans.ttf]

`wettergraph/app/icons/` is generated: every run wipes and rebuilds it.

The contact sheet draws its labels with DejaVu Sans. Pass `--font` where the box
has no font, otherwise labels come out blank (resvg draws text as nothing when
no font answers) - the icons themselves are unaffected.
"""

import argparse
import base64
import copy
import json
import re
import shutil
import struct
import sys
import zlib
from pathlib import Path
import xml.etree.ElementTree as ET

try:
    import resvg_py
except ImportError:  # pragma: no cover
    sys.exit("resvg-py is required: uv run --with resvg-py python tools/extract-icons.py")

ROOT = Path(__file__).resolve().parents[1]
METEOGRAM = ROOT / "tools" / "assets" / "meteogram-6325496.svg"
WEBP_DIR = ROOT / "wettergraph" / "app" / "source-webp"
ICONS = ROOT / "wettergraph" / "app" / "icons"
SHEET = ROOT / "tools" / "assets" / "icon-contact-sheet.png"  # a build artifact, not committed

SVG = "{http://www.w3.org/2000/svg}"
XLINK = "{http://www.w3.org/1999/xlink}"
ET.register_namespace("", "http://www.w3.org/2000/svg")
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

# The met.no symbol codes the graph can receive. The list is MET's own:
# `metno/weathericons` weather/legend.csv plus the day/night/polartwilight
# suffixes documented for locationforecast 2.0. The extra-s spelling of
# lightssleetshowersandthunder / lightssnowshowersandthunder is MET's typo and
# is kept on purpose - it is what the API sends.
VARIANT_BASES = (
    "clearsky", "fair", "partlycloudy",
    "lightrainshowers", "rainshowers", "heavyrainshowers",
    "lightrainshowersandthunder", "rainshowersandthunder", "heavyrainshowersandthunder",
    "lightsleetshowers", "sleetshowers", "heavysleetshowers",
    "lightssleetshowersandthunder", "sleetshowersandthunder", "heavysleetshowersandthunder",
    "lightsnowshowers", "snowshowers", "heavysnowshowers",
    "lightssnowshowersandthunder", "snowshowersandthunder", "heavysnowshowersandthunder",
)
SINGLE_BASES = (
    "cloudy", "lightrain", "rain", "heavyrain",
    "lightrainandthunder", "rainandthunder", "heavyrainandthunder",
    "lightsleet", "sleet", "heavysleet",
    "lightsleetandthunder", "sleetandthunder", "heavysleetandthunder",
    "lightsnow", "snow", "heavysnow",
    "lightsnowandthunder", "snowandthunder", "heavysnowandthunder",
    "fog",
)
SUFFIX = ("day", "night", "polartwilight")
SYMBOL_CODES = sorted(
    [f"{base}_{suffix}" for base in VARIANT_BASES for suffix in SUFFIX] + list(SINGLE_BASES)
)

# MET's legacy numeric symbol id -> base symbol code, from weathericons
# weather/legend.csv. The meteogram keys its art with `<old id><d|n|m>`.
LEGACY_IDS = {
    1: "clearsky", 2: "fair", 3: "partlycloudy", 4: "cloudy",
    5: "rainshowers", 6: "rainshowersandthunder", 7: "sleetshowers", 8: "snowshowers",
    9: "rain", 10: "heavyrain", 11: "heavyrainandthunder", 12: "sleet", 13: "snow",
    14: "snowandthunder", 15: "fog",
    20: "sleetshowersandthunder", 21: "snowshowersandthunder", 22: "rainandthunder",
    23: "sleetandthunder", 24: "lightrainshowersandthunder",
    25: "heavyrainshowersandthunder", 26: "lightssleetshowersandthunder",
    27: "heavysleetshowersandthunder", 28: "lightssnowshowersandthunder",
    29: "heavysnowshowersandthunder", 30: "lightrainandthunder", 31: "lightsleetandthunder",
    32: "heavysleetandthunder", 33: "lightsnowandthunder", 34: "heavysnowandthunder",
    40: "lightrainshowers", 41: "heavyrainshowers", 42: "lightsleetshowers",
    43: "heavysleetshowers", 44: "lightsnowshowers", 45: "heavysnowshowers",
    46: "lightrain", 47: "lightsleet", 48: "heavysleet", 49: "lightsnow", 50: "heavysnow",
}

FONT_CANDIDATES = (
    Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)


def refs_of(el):
    """All ids an element tree references via url(#id) or an href="#id"."""
    out = set()
    for e in el.iter():
        for name, value in e.attrib.items():
            if "url(#" in value:
                out.add(value.split("#", 1)[1].rstrip(")"))
            if name.endswith("href") and value.startswith("#"):
                out.add(value[1:])
    return out


def family_code(family):
    """`03n` -> `partlycloudy_night` via MET's legacy id table."""
    m = re.fullmatch(r"(\d+)([dnm]?)", family)
    if not m:
        raise SystemExit(f"unrecognised icon family id: {family!r}")
    legacy = int(m.group(1))
    if legacy not in LEGACY_IDS:
        raise SystemExit(f"icon family {family!r}: legacy id {legacy} is not in MET's table")
    code = LEGACY_IDS[legacy]
    if m.group(2):
        code += "_" + {"d": "day", "n": "night", "m": "polartwilight"}[m.group(2)]
    return code


def load_meteogram():
    """Return (families, defs): first art block and every <defs> child, by id."""
    root = ET.parse(METEOGRAM).getroot()
    slots = []

    def walk(el):
        if el.tag == SVG + "svg" and el.get("width") == "24" and el.get("height") == "24":
            slots.append(el)
            return
        for child in el:
            walk(child)

    walk(root)

    defs = {}
    for d in root.iter(SVG + "defs"):
        for child in d:
            child_id = child.get("id")
            if child_id and child_id not in defs:
                defs[child_id] = child

    families = {}
    for slot in slots:
        fams = {i.split("__")[0] for i in refs_of(slot) if "__" in i}
        if not fams:
            continue  # a 24x24 UI glyph (pin, legend), not weather art
        if len(fams) != 1:
            raise SystemExit(f"icon slot references several families: {sorted(fams)}")
        family = fams.pop()
        families.setdefault(family, slot[0])
    return families, defs


def needed_defs(art_elements, defs):
    """Every def the art pulls in, transitively."""
    needed = set()
    queue = [refs_of(el) for el in art_elements]
    while queue:
        for ref in queue.pop():
            if ref in needed:
                continue
            needed.add(ref)
            if ref in defs:
                queue.append(refs_of(defs[ref]))
    return needed


def rewrite_refs(el, mapping):
    """Point every url(#id)/href="#id" at the id it was renamed to."""
    for e in el.iter():
        for name, value in list(e.attrib.items()):
            if "url(#" in value or (name.endswith("href") and value.startswith("#")):
                for old, new in mapping.items():
                    value = value.replace("#" + old, "#" + new)
                e.set(name, value)


def vector_icon(code, family, inner, defs):
    """One standalone SVG built from the meteogram art of `family`."""
    art = [c for c in inner if c.tag != SVG + "defs"]
    rename = {
        old: f"{code}__{old.split('__')[-1]}"
        for old in sorted(needed_defs(art, defs))
    }
    out = ET.Element(SVG + "svg", {"viewBox": "0 0 100 100", "width": "100", "height": "100"})
    d = ET.SubElement(out, SVG + "defs")
    for old in sorted(rename):
        if old not in defs:
            raise SystemExit(f"{family}: reference to unknown def {old!r}")
        node = copy.deepcopy(defs[old])
        node.set("id", rename[old])
        rewrite_refs(node, rename)
        d.append(node)
    group = ET.SubElement(out, SVG + "g", {"id": code})  # the stable handle
    for a in art:
        node = copy.deepcopy(a)
        rewrite_refs(node, rename)
        group.append(node)
    return out


def webp_path_for(code):
    """The app webp for a symbol code, tolerating MET's extra-s/spelling split."""
    direct = WEBP_DIR / f"weather_icon_{code}.webp"
    if direct.exists():
        return direct
    alias = WEBP_DIR / f"weather_icon_{code.replace('lightss', 'lights', 1)}.webp"
    if alias.exists():
        return alias
    return None


def webp_icon(code, path):
    """One standalone SVG carrying the webp art as a data URI."""
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    out = ET.Element(SVG + "svg", {"viewBox": "0 0 100 100", "width": "100", "height": "100"})
    group = ET.SubElement(out, SVG + "g", {"id": code})
    ET.SubElement(
        group,
        SVG + "image",
        {
            "x": "0", "y": "0", "width": "100", "height": "100",
            XLINK + "href": f"data:image/webp;base64,{data}",
        },
    )
    return out


def png_has_ink(png):
    """True when a rendered PNG holds at least one non-transparent pixel."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat = 8, b""
    width = height = depth = color = interlace = None
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos:pos + 4])
        chunk = png[pos + 4:pos + 8]
        data = png[pos + 8:pos + 8 + length]
        pos += 12 + length
        if chunk == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", data)
        elif chunk == b"IDAT":
            idat += data
        elif chunk == b"IEND":
            break
    if depth != 8 or color not in (2, 6) or interlace != 0:
        raise ValueError(f"unsupported PNG: depth={depth} color={color} interlace={interlace}")
    channels = 3 if color == 2 else 4
    stride = width * channels
    raw = zlib.decompress(idat)
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        filter_type = raw[pos]
        pos += 1
        row = bytearray(raw[pos:pos + stride])
        pos += stride
        for x in range(stride):
            left = row[x - channels] if x >= channels else 0
            above = prev[x]
            upper_left = prev[x - channels] if x >= channels else 0
            if filter_type == 1:
                row[x] = (row[x] + left) & 0xFF
            elif filter_type == 2:
                row[x] = (row[x] + above) & 0xFF
            elif filter_type == 3:
                row[x] = (row[x] + (left + above) // 2) & 0xFF
            elif filter_type == 4:
                pa, pb, pc = abs(above - upper_left), abs(left - upper_left), abs(left + above - 2 * upper_left)
                predictor = left if (pa <= pb and pa <= pc) else (above if pb <= pc else upper_left)
                row[x] = (row[x] + predictor) & 0xFF
        for x in range(width):
            i = x * channels
            if channels == 4:
                if row[i + 3] > 0:
                    return True
            elif row[i:i + 3] != b"\xff\xff\xff":
                return True
        prev = row
    return False


def find_font(explicit):
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise SystemExit(f"font not found: {path}")
        return path
    for candidate in FONT_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def apply_sheet_text(parts, x, y, text, size, fill):
    parts.append(
        f'<text x="{x}" y="{y}" font-family="DejaVu Sans" font-size="{size}" fill="{fill}">{text}</text>'
    )


# The two plot grounds an icon actually sits on (graph-spec §0, render.PALETTES).
THEME_GROUNDS = (("#ffffff", "#c3d0d8"), ("#020a14", "#374759"))
PLOT_STEP, GRID_DY = 12.2373, 12.0  # px per hour, px per °C row
ICON_24 = 24


def plot_patch(parts, x, y, inner, background, grid):
    """A 24 px icon on a 4 x 3 cell cut of the plot, centred on an hour line.

    Like a render, the box's left edge lands off the pixel grid (odd hour x
    STEP - 12), so resvg resamples the art the way the dashboard sees it.
    """
    w, h = 4 * PLOT_STEP, 3 * GRID_DY + 4
    parts.append(f'<rect x="{x}" y="{y}" width="{w:.4f}" height="{h}" fill="{background}"/>')
    for c in range(5):
        gx = x + c * PLOT_STEP
        parts.append(f'<line x1="{gx:.4f}" y1="{y}" x2="{gx:.4f}" y2="{y + h}" stroke="{grid}" stroke-width="1"/>')
    for r in range(4):
        gy = y + 2 + r * GRID_DY
        parts.append(f'<line x1="{x}" y1="{gy}" x2="{x + w:.4f}" y2="{gy}" stroke="{grid}" stroke-width="1"/>')
    left = x + 2 * PLOT_STEP - ICON_24 / 2
    parts.append(
        f'<svg x="{left:.4f}" y="{y + 7}" width="{ICON_24}" height="{ICON_24}" viewBox="0 0 100 100">{inner}</svg>'
    )


def make_sheet(codes, kinds):
    cols, cell_w, cell_h = 7, 200, 136
    margin, header = 16, 44
    rows = (len(codes) + cols - 1) // cols
    width = margin * 2 + cols * cell_w
    height = header + rows * cell_h + margin
    parts = [
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
    ]
    vectors = sum(1 for kind in kinds.values() if kind == "vector")
    apply_sheet_text(
        parts, margin, 22,
        f"Wettergraph icon set - {len(codes)} symbol codes ({vectors} vector, "
        f"{len(codes) - vectors} webp) - 92 px, 28 px (retired), 24 px on light and dark plot",
        13, "#222222",
    )
    apply_sheet_text(parts, margin, 38, "generated by tools/extract-icons.py", 9, "#888888")

    for i, code in enumerate(codes):
        x = margin + (i % cols) * cell_w
        y = header + (i // cols) * cell_h
        inner = (ICONS / f"{code}.svg").read_text()
        inner = inner[inner.index(">") + 1:inner.rindex("</")]
        parts.append(f'<svg x="{x}" y="{y}" width="92" height="92" viewBox="0 0 100 100">{inner}</svg>')
        parts.append(f'<svg x="{x + 100}" y="{y + 4}" width="28" height="28" viewBox="0 0 100 100">{inner}</svg>')
        for n, (background, grid) in enumerate(THEME_GROUNDS):
            plot_patch(parts, x + 136, y + n * 46, inner, background, grid)
        base, _, suffix = code.partition("_")
        apply_sheet_text(parts, x + 2, y + 106, base, 9, "#333333")
        if suffix:
            apply_sheet_text(parts, x + 2, y + 118, suffix, 9, "#999999")

    return (
        f'<svg xmlns="{SVG[1:-1]}" xmlns:xlink="{XLINK[1:-1]}" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">' + "".join(parts) + "</svg>",
        width,
        height,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--font", help="TTF for the contact-sheet labels (default: DejaVu paths)")
    args = parser.parse_args()

    font = find_font(args.font)
    families, defs = load_meteogram()

    vector = {family_code(family): family for family in families}
    unknown = sorted(set(vector) - set(SYMBOL_CODES))
    if unknown:
        raise SystemExit(f"meteogram art maps to unknown symbols: {unknown}")

    if ICONS.exists():
        shutil.rmtree(ICONS)
    ICONS.mkdir(parents=True)

    index_icons = {}
    kinds = {}
    for code in SYMBOL_CODES:
        if code in vector:
            family = vector[code]
            node = vector_icon(code, family, families[family], defs)
            art, kind = f"meteogram:{family}", "vector"
        else:
            path = webp_path_for(code)
            if path is None:
                raise SystemExit(f"no art for {code}: neither meteogram nor {WEBP_DIR}")
            node = webp_icon(code, path)
            art, kind = f"webp:{path.name}", "webp"
        (ICONS / f"{code}.svg").write_text(ET.tostring(node, encoding="unicode") + "\n")
        index_icons[code] = {"file": f"{code}.svg", "art": art, "kind": kind}
        kinds[code] = kind

    index = {
        "generated_by": "tools/extract-icons.py",
        "sources": {
            "vector": str(METEOGRAM.relative_to(ROOT)),
            "webp": str(WEBP_DIR.relative_to(ROOT)) + "/weather_icon_*.webp",
        },
        "contract": (
            'each file is an SVG with viewBox="0 0 100 100"; the art hangs on a '
            '<g id="<symbol_code>"> element'
        ),
        "counts": {
            "total": len(SYMBOL_CODES),
            "vector": sum(1 for k in kinds.values() if k == "vector"),
            "webp": sum(1 for k in kinds.values() if k == "webp"),
        },
        "icons": index_icons,
    }
    (ICONS / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")

    blank = []
    for code in SYMBOL_CODES:
        png = bytes(resvg_py.svg_to_bytes(svg_path=str(ICONS / f"{code}.svg"), width=100, height=100))
        if not png_has_ink(png):
            blank.append(code)
    if blank:
        raise SystemExit(f"these icons rendered blank: {blank}")

    sheet_svg, width, height = make_sheet(SYMBOL_CODES, kinds)
    sheet_png = resvg_py.svg_to_bytes(
        svg_string=sheet_svg,
        width=width,
        height=height,
        background="#ffffff",
        font_files=[str(font)] if font else None,
    )
    SHEET.write_bytes(bytes(sheet_png))

    print(f"icons:   {len(SYMBOL_CODES)} files in {ICONS.relative_to(ROOT)}")
    print(f"index:   {(ICONS / 'index.json').relative_to(ROOT)}")
    print(f"sheet:   {SHEET.relative_to(ROOT)}")
    print(f"vector:  {index['counts']['vector']}  webp: {index['counts']['webp']}")
    if font is None:
        print("warning: no DejaVu font found; the contact sheet has no labels (pass --font)")


if __name__ == "__main__":
    main()
