# yr's dark meteogram: palette and what else differs

Findings for ticket `issues/02-yr-dark-palette.md`. Read-only research against the two SVGs;
no renderer code touched.

## Sources

| | |
|---|---|
| Dark | `https://www.yr.no/en/content/2-6325496/meteogram.svg?mode=dark` - HTTP 200, 141 472 B, `image/svg+xml`, fetched 2026-09-23 |
| Dark, saved as | `tools/assets/meteogram-6325496-dark.svg` |
| Light | `tools/assets/meteogram-6325496.svg` (fetched 2026-09-20) |

The place id in the map (`2-6325496`) served the dark variant directly. The fallback id
`2-2867714` was **not** needed and was not tried.

**Caveat that shapes every comparison below:** the two files are three days apart, so they carry
different forecasts - different dates, hours, temperatures, symbol codes, and 60 vs 59 hourly
points. Every numeric difference traced back to that, not to `mode=dark`. Where a number below is
called "identical" it is byte-identical across the two files.

## Light -> dark colour table

Everything we clone. Values are verbatim from the files, `#rrggbb` as yr writes them.

| Element | Where it is defined | Light | Dark |
|---|---|---|---|
| Canvas background | `style="background-color:…"` on `<svg>` **and** `<rect x=0 y=0 width=782 height=391>` | `#ffffff` | `#020a14` |
| Cell grid lines | `<line stroke=…>`, 134 light / 132 dark | `#c3d0d8` | `#374759` |
| Day separator lines | `<line stroke=…>`, 4 each | `#56616c` | `#c3d0d8` |
| Temperature curve, above 0 °C | `#temperature-curve-gradient` stop 1 | `#c60000` | `#ff2d3f` |
| Temperature curve, below 0 °C | same gradient, stops 2 and 3 | `#006edb` | `#00b8f1` |
| Rain bars | `<rect fill=…>`, one per hour | `#006edb` | `#00b8f1` |
| Default text fill (`text {}`) | CSS in `<style>` | `#21292b` | `#ffffff` |
| Day labels (`.day-label`) | inherits `text {}` | `#21292b` | `#ffffff` |
| Hour labels (`.hour-label`) | CSS | `#56616c` | `#a2a5b3` |
| °C / mm axis labels (`.y-axis-label`) | CSS | `#56616c` | `#a2a5b3` |
| Legend text (`.legend-label`) | CSS | `#56616c` | `#a2a5b3` |
| Over-max rain value (`.precipitation-values-over-max`) | CSS | `#ffffff` | `#21292b` |
| Legend swatch - temperature | `<rect y="40%" height="20%">` | `#c60000` | `#ff2d3f` |
| Legend swatch - precipitation | `<rect y=0 height="100%">` | `#006edb` | `#00b8f1` |
| Axis unit glyphs (°/mm icons at `x=-17`, `x=727`) | CSS `filter:` on a `currentColor` glyph | `invert(38%) sepia(9%) saturate(714%) hue-rotate(169deg) brightness(93%) contrast(89%)` | `invert(70%) sepia(8%) saturate(388%) hue-rotate(192deg) brightness(95%) contrast(85%)` |

Two notes on that table:

- **The day separator in dark is `#c3d0d8` - the light mode's *grid* colour.** yr reuses the value
  at a different job. Anyone grepping for `#c3d0d8` across both files will hit grid lines in one and
  separators in the other.
- **The over-max rain label inverts** (`#ffffff` -> `#21292b`): it is printed *on top of* a clipped
  rain bar, so it takes the colour that contrasts with the bar, not with the background.

Out of scope but recorded, since they sit in the same markup: the wind legend swatch and wind
arrows go `#aa00f2` -> `#c438ff`, and the wind arrow glyph filter goes
`invert(15%) sepia(6%) saturate(995%) hue-rotate(145deg) brightness(99%) contrast(99%)` ->
`invert(99%) sepia(0%) saturate(0%) hue-rotate(146deg) brightness(104%) contrast(100%)` (black ->
white). The yr/NRK header logo also flips from `filter="none"` to that same white filter.

## Does anything but colour differ?

**No - with exactly one exception, the night-symbol moon.** Checked item by item:

### Identical

- **Canvas:** `width="782" height="391"` in both; background rect `0 0 782 391` in both.
- **Font sizes and weights:** the `<style>` block's size/weight/line-height/letter-spacing rules are
  byte-identical. Only the six `fill:` declarations at the end of the block differ. Same font stack
  (`NRK Sans Variable, …`) and the same external `nrk-sans.min.css` stylesheet link.
- **Layout transforms:** every group transform matches exactly - `30 0`, `30 24`, `30 48`, `30 168`,
  `30 180`, `30 252`, `translate(30, 278)` for the legend, and the legend's internal
  `0 / 126 / 258` column offsets.
- **Plot band:** horizontal grid lines run `x1=0 x2=722` at the same y values (`120, 108, 96, …`) in
  both - so the 120 px band height and the 24 px-per-6 °C axis step are unchanged.
- **Axis labels:** `.y-axis-label` at `x="-5"`, `y="23.999999999999993"`, `y="48"`, … identical.
- **Hour labels:** `y="9" dy="0.35em" text-anchor="middle"` identical.
- **Legend:** swatch `<svg x="0" y="4" width="10" height="10">`, text at `x="14" y="9" dy="0.35em"` -
  identical in both.
- **Stroke widths:** both files carry exactly `stroke-width="1.5"` x3 and `stroke-width="2"` x2, and
  18 empty `stroke-dasharray=""`. The temperature curve is `stroke-width="2"` in both.
- **Temperature gradient element:** `x1=0 y1=0 x2=0 y2=120 gradientUnits="userSpaceOnUse"
  spreadMethod="pad"` - identical. Only the stop colours change. (The stop *offset* reads `110%`
  light vs `120%` dark, but that offset encodes where 0 °C falls in the band and is recomputed per
  forecast; both being >100% just means every value was above freezing on both days.)
- **Opacity:** the only `opacity` values in either file are `0`, `.3` and `.6`, all inside weather
  icon art, at the same values. No mode-level opacity or overlay layer.
- **Rain bars:** same baseline `y="120"`, same first bar `x="0.5"`, same `width = column - 1`
  formula, same `height="0"` for dry hours.

### The one real non-colour difference: night icons are recoloured art

The weather symbols are inline `<svg viewBox="0 0 100 100">` groups keyed by MET symbol code, and
they are **not** filtered - yr ships the art itself. Comparing the codes present in both files, with
Figma's per-build `effect…_NNN_NNNN` filter ids normalised away:

| Code | Verdict |
|---|---|
| `01d` clear day | identical |
| `02d` fair day | identical |
| `03d` partly cloudy day | identical |
| `04` cloudy | identical |
| `01n` clear night | **differs** - moon gradient only |
| `03n` partly cloudy night | **differs** - moon gradient only |

For both night codes every `d=` path string is identical; what changes is the moon disc's gradient
stops:

```
light:  #686E73 -> #6A7075     (grey moon)
dark:   #C7B789 -> #E1C578     (warm cream moon)
```

Cloud fills inside those same night icons (`#DDD`, `#B6B6B6`, `#B3B2B2`) do **not** change. So the
rule is narrow: **in dark mode the moon turns warm cream; everything else about the icon art is the
same file.** Day icons and cloud/sun art are untouched.

(`#B2B2B2` and `#ACACAC` appear only in the dark file, and `#aa00f2`-adjacent codes only in the
light one. Both are an artefact of the different forecasts - those greys live inside codes `09` and
`15`, which only the 2026-09-23 payload happened to contain. Not a palette difference.)

## One thing worth flagging to the spec ticket

The map records "12.2373 px per hour" as one of yr's own numbers. That figure is **derived, not
fixed**: the plot is always 722 px wide and yr divides it by `n - 1` points.

- light, 60 points -> `722 / 59 = 12.2373`
- dark, 59 points -> `722 / 58 = 12.4483`

The map has already settled on a fixed 60 h / 61-point window, which computes its own step
(`722 / 60 = 12.0333` if the 722 px plot width were kept). Nothing here contradicts that decision -
but the `12.2373` constant should be treated as "what yr produced for a 60-point payload", not as a
column width to hard-code.

## Bottom line

Cloning dark is a pure palette swap over the light geometry - one background, one grid colour, one
separator colour, two curve stops, one rain colour, three text colours, one inverted over-max label,
two legend swatches - **plus** swapping the moon gradient in the night symbols from grey to cream.
No geometry, font-size, stroke-width or opacity change anywhere.
