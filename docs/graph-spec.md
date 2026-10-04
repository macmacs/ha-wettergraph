# Graph visual specification

The spec the renderer, the app and their checks are written to. Ticket 02,
rewritten by redesign ticket 03.
The graph is a clone of yr's meteogram with the wind band and the yr/NRK header
removed. Every clause is a number, a rule, or an explicit "not shown", so
implementation tickets can cite it (`spec §4.2`).

**How to read the numbers.** Every length is stated at the design width
`W0 = 794.2373`. At any other width `W`, multiply by `k = W / 794.2373` and
round to 0.1 px. Text, strokes and the 24 px icon box scale by `k` too; label
density never changes with width, so the layout is self-similar.

**Where the numbers come from.** The geometry is not designed here, it is
cloned. `§0` lists every constant with its source in yr's own file. The
reference render is `tools/assets/graph-reference-v2.svg`
(dry, yr's own forecast) and `tools/assets/graph-reference-v2-rain.svg` (a wet
sample including an over-max bar), both built by `tools/make-reference-v2.py`,
which reads its constants out of `tools/assets/meteogram-6325496.svg` at build
time. The dark palette is `docs/dark-palette.md`, read off
`tools/assets/meteogram-6325496-dark.svg`. Nothing fetches yr.no at runtime; the original is
a layout source only.

## §0 Cloned constants

Every row is verbatim from `tools/assets/meteogram-6325496.svg` unless the note says
otherwise. Clauses below refer to these by name instead of repeating literals.

| Name | Value | Source |
| --- | --- | --- |
| hour step | `12.2373` (`722 / 59`) | yr's plot is 722 px over 59 intervals. We hold the density and let the canvas follow, so this is fixed, not derived from the payload |
| plot | `734.2373 x 120` | `60 x` hour step wide; the 120 px band height is yr's |
| gutters | left `30`, right `30` | yr's plot group sits at `x=30` in a 782 px canvas |
| canvas | `794.2373 x 210` | `30 + 734.2373 + 30` by the row stack of §3.3 |
| cell grid | horizontal every `12`, vertical every hour step | 11 horizontal lines (`y=0..120`), 61 vertical |
| axis label pitch | every `24` px (every second grid line) | five labels, `y=24..120`; `y=0` unlabelled |
| mm scale | `12` px/mm, fixed `0..10 mm` over the 120 px band | `0` at `y=120` |
| bar | `x = i * step + 0.5`, width `11.2373` (`step - 1`) | yr's own bar geometry: half a pixel of air either side |
| curve | stroke `2`, no fill, Catmull-Rom to cubic Bézier, tension `1/6`, both ends duplicated | verified against yr's first two segments to the 4th decimal |
| icon box | `24 x 24`, every 2 h, centred on the odd point index | `x = i * step - 12` |
| font sizes | `16` day, `13` hour/axis/legend, `12` over-max and age chip | yr's rem values at a 15 px root: `1.0666667rem`, `0.8666667rem`, `0.8rem` |
| row offsets | day `8`, hour `32`, plot `56`, legend `186` | yr's inner offsets `+0 / +24 / +48`, header replaced by an 8 px margin, wind band cut out |
| legend | entries at `x=0` and `x=126`, swatch `10 x 10` at `y=4`, text `x=14 y=9 dy=0.35em` | yr's own offsets, wind entry dropped |

## §1 Output surface and size

- **§1.1** The renderer emits an **SVG**: the published artifact, written to
  `www/wettergraph/graph-{light,dark}.svg` and served by HA at `/local/`. Its
  intrinsic size is `W x round(W * 210 / 794.2373)`; `W = 794` unless set. The
  PNG routes rasterise the same SVG at the same size and stay for the Generic
  Camera, which HA fetches server-side.
- **§1.2** `W` is clamped to `560 <= W <= 1588`. A request outside the range is
  clamped, not refused. The floor is rasterisation, not collision: the layout is
  self-similar, so nothing ever overlaps, but at `520` the first hour label's
  two digits merge and at `480` the text leaves full `#56616c` and the cell grid
  drops under half contrast. `560` is the last width where every digit splits
  and the text is full-colour. The ceiling is `2 x` the design width, where
  nothing further is gained. Measured, not guessed - a sweep of the reference
  render from `1588` px down to `320`.
- **§1.3** The height is never set directly; it follows the design canvas ratio
  `210 / 794.2373` (about 1:3.78). There is no fixed aspect contract: the
  dashboard card scales the SVG to its tile.
- **§1.4** The image is opaque: a full-canvas background rect, no transparency,
  no rounded corners, no border of its own. The card draws the frame.
- **§1.5** One look, one route. No `?style=` toggle and no second layout to
  maintain: this spec replaced the previous one in place.

## §2 Theme

- **§2.1** Two variants: `light` and `dark`. Same geometry, same layout, only
  the palette changes - which is yr's own arrangement, where `?mode=dark`
  changes six `fill:` rules and two gradient stops and nothing else.
  `light` is the default.
- **§2.2** The variant is selected per request with `theme=light|dark`. An
  unknown value falls back to `light`.
- **§2.3** Palette:

| Element | light | dark |
| --- | --- | --- |
| Background | `#ffffff` | `#020a14` |
| Cell grid | `#c3d0d8` | `#374759` |
| Day separator | `#56616c` | `#c3d0d8` |
| Day label | `#21292b` | `#ffffff` |
| Hour, axis and legend text | `#56616c` | `#a2a5b3` |
| Axis glyphs (thermometer, droplet) | `#56616c` | `#a2a5b3` |
| Curve above 0 °C | `#c60000` | `#ff2d3f` |
| Curve below 0 °C | `#006edb` | `#00b8f1` |
| Rain bar | `#006edb` | `#00b8f1` |
| Over-max rain value | `#ffffff` | `#21292b` |
| Age chip text and dot | `#56616c` | `#a2a5b3` |
| Night shade (§4.9) | `#21292b` at `0.08` | none |
| Day shade (§4.9) | none | `#c3d0d8` at `0.08` |

  Two traps this table hides: the dark day separator is `#c3d0d8`, the same hex
  light mode uses for its *grid*, and the over-max value inverts, because it is
  drawn on the bar rather than on the background.

## §3 Canvas layout

- **§3.1** The canvas is `794.2373 x 210` (§0). There is no page padding: the
  margins are the gutters of §3.2 and the row stack of §3.3.
- **§3.2** The plot is `734.2373 x 120` at `x = 30`, leaving a `30` px gutter
  either side. The left gutter carries the °C axis, the right gutter the mm
  axis (§3.5).
- **§3.3** One band, not two. Rows from the top: `8` px margin, day-label row
  `24`, hour-label row `24`, plot `120`, `10` px gap, legend row `18`, `6` px
  bottom margin. `8 + 24 + 24 + 120 + 10 + 18 + 6 = 210`. The wind band is
  gone (§9.1) and with it the second band; the legend follows the plot at the
  same 10 px gap it used after yr's wind arrows.
- **§3.4** Text is `DejaVu Sans, Verdana, sans-serif` (Alpine package
  `font-dejavu`), at the §0 font sizes. The renderer loads
  `/usr/share/fonts/dejavu/DejaVuSans.ttf` by path, not by font discovery, and
  resvg never reaches past the first family; the rest of the stack is for a
  browser shown the SVG itself, which is now the normal case (the card loads
  the SVG, not the PNG). If that file cannot be read, the app logs an error at
  start and keeps rendering: resvg draws text as nothing when no font answers,
  so a missing font silently empties every number on the PNG.
- **§3.5** Both axes are labelled, each in its own gutter, vertically centred
  on their grid line at the §0 label pitch:
  - °C at `x = -5` relative to the plot, `text-anchor="end"`, five labels at
    `y = 24, 48, 72, 96, 120`, `y = 0` unlabelled. Format `27°`.
  - mm at `x = plot + 5`, `text-anchor="start"`, the same five `y`, reading
    `8, 6, 4, 2, 0`. No unit in the text.
  - yr's 24 px thermometer and droplet glyphs at `scale(0.5)`, at
    `translate(-17, -6)` and `translate(plot + 5, -6)`. yr tints them with a
    CSS `filter:`; we set the fill directly, because resvg does not do filter
    functions.
- **§3.6** The left gutter is 30 px and the °C label is right-aligned into it.
  `27°` is 23.0 px of ink at 13 px, but `-10°` is 27.7 px and, anchored at
  `x = -5`, would be clipped by the canvas edge. When the band bottom is
  `<= -10 °C`, the degree sign is therefore dropped from **every** °C label of
  that render (`-10`, 22.3 px, fits); the thermometer glyph already carries the
  unit. The canvas never widens for a label.
- **§3.7** The legend row carries two entries at `x = 0` and `x = 126` (§0):
  a `10 x 10` swatch at `y = 4` and a `13` px muted label at
  `x = 14, y = 9 dy="0.35em"`. Temperature: the swatch is a bar,
  `y="40%" height="20%"`, in the warm colour. Precipitation: the swatch is
  filled whole, in the rain colour. Texts are `Temperatur °C` and
  `Niederschlag mm`. yr's wind entry at `x = 258` is dropped.

## §4 Time window and time axis

- **§4.1** The window is always `60` hours, step `1` hour, `61` points, starting
  at the hour of the render in the app's local time zone. Fixed: a short
  payload leaves no stretched grid, it is filled by §4.8.
  The local zone is `TZ` if it names a real zone, else `time_zone` from HA's
  `.storage/core.config`, else UTC with a failing startup check
  (`localzone.py`); Supervisor's `TZ` was observed not to arrive.
- **§4.2** Hourly temperature points are joined by a Catmull-Rom spline,
  converted to cubic Bézier segments, tension `1/6`, both ends duplicated (§0).
  No resampling, no averaging.
- **§4.3** The cell grid is drawn behind everything: a vertical line at every
  one of the 61 points, full plot height, and a horizontal line every `12` px
  (11 lines, including the plot's top and bottom edge). 1 px, grid colour. The
  plot has no separate frame; the outermost grid lines are the frame.
- **§4.4** A vertical line at each local midnight is drawn in the day-separator
  colour instead of the grid colour. Same 1 px, same geometry: it replaces the
  grid line, it does not add one.
- **§4.5** Day labels sit in the day row, `16` px, weight `600`, day-label
  colour, `text-anchor="start"`, `x` = the boundary, `y = 12 dy="0.35em"`.
  Format is German, numeric: `So 20.09.` Weekdays are `Mo Di Mi Do Fr Sa So`.
  The opening, partial day is labelled at `x = 0` like any other.
- **§4.6** A day label is drawn only if at least `6.5` h of window remain to its
  right before the next day boundary or the window end; otherwise it is
  dropped. A label is 69-76 px of ink (75.9 px for `So 20.09.` at 16 px)
  and 6.5 h is 79.5 px, so this is exactly the no-overlap, no-clip condition.
  It fires at both edges: the opening day loses its label for a window
  starting 18:00-23:00, and the closing day loses its label for one starting
  12:00-18:00, where it would otherwise run off the canvas. A window starting
  at 18:00 drops both.
- **§4.7** Hour labels sit in the hour row, `13` px, muted colour,
  `text-anchor="middle"`, `x = i * step`, `y = 9 dy="0.35em"`, at every even
  point index **except the last** (`i = 0, 2, ..., 58`): 30 labels. The final
  point's label would sit on the mm axis. Format is two digits, `08`.
- **§4.8** met.no's payload carries 61 hourly entries from its own first hour
  and 6-hourly entries after that. Our window starts at the *render* hour, so
  on any render made an hour or more after the last poll the tail runs past the
  hourly entries: that is the normal case, not an edge case. A window slot with
  no hourly entry is **interpolated linearly** between the nearest entries the
  payload does carry, temperature and precipitation alike (precipitation is
  already normalised to mm/h), and its symbol code is taken from the entry
  covering it. The plot therefore reaches the right edge on every render.
  A slot that cannot be filled at all - no entry on either side - is a gap: the
  curve breaks, no bar, no icon.
- **§4.9** Day/night shading, cloned from the Android Wettergraph widget, not
  from yr. One rect over the whole plot band, drawn **behind the grid**, filled
  with one horizontal gradient. The sun times are Home Assistant's own
  `sun.sun` (`next_rising`, `next_setting`), extrapolated in 24 h steps across
  the window; in polar day or night (next event more than a day away) only the
  real events and the entity's state are used. Each sunrise and sunset is a
  linear ramp from `1 h` before to `1 h` after the event, capped at half the
  gap to its neighbour. Light shades the night, dark lifts the day (§2.3); the
  other side stays the background. No `sun.sun` reading draws no shade. On by
  default; the `day_night` option and `?daynight=0|1` per request switch it.

## §5 Temperature axis and curve

- **§5.1** The band is always 120 px and 10 grid rows; the degrees per row are
  fitted to the window, which is yr's own arrangement (its light sample runs
  3 °C per row, its dark one 2 °C). Let `r` be the first of
  `1, 2, 3, 5, 10` °C per row for which
  `bottom = floor(t_min / r) * r`, `top = bottom + 10 * r` leaves at least
  `29` px of headroom above `t_max` (§6.3), i.e.
  `(top - t_max) * (12 / r) >= 29`. Scale is `12 / r` px per °C. If no `r`
  qualifies, `r = 10` is used and the icons clamp (§6.3). Fitted against the
  window after §4.8, so an interpolated tail counts.
  The rule reproduces yr's dark sample exactly (`4 .. 24`, `r = 2`) and yr's
  step for the light one.
- **§5.2** Axis labels are the §3.5 five, `bottom + (120 - y) / (12 / r)`,
  always whole degrees because `r` is whole and the pitch is two rows.
- **§5.3** Curve stroke is `2` px, no fill, no point markers, no glow. It is
  drawn over the rain bars and under the icons.
- **§5.4** The curve is not clipped to the band: the fit of §5.1 keeps `t_min`
  and `t_max` inside it by construction.
- **§5.5** Warm and cold are one path with one gradient, not two paths -
  yr's whole mechanism. A `linearGradient` with
  `gradientUnits="userSpaceOnUse"`, `y1=0 y2=120`, `spreadMethod="pad"`, and
  two **coincident** stops at `offset = y(0 °C) / 120` (warm above, cold
  below), plus a trailing cold stop at `100%`. The offset is allowed outside
  `0..100%` - `pad` then paints the whole band in one colour, which is the
  correct result for an all-warm or all-frozen window.
- **§5.6** The unit rides on the axis glyph (§3.5), not on a label and not in
  an axis title. Labels read `27°`, or `27` under §3.6.
- **§5.7** Celsius only. No conversion, no unit option, no `°F` path. A payload
  in another unit is refused by the data layer.

## §6 Icons

- **§6.1** One icon every `2` h, 30 icons in the window, centred on the **odd**
  point index (`x = i * step - 12`, `i = 1, 3, ..., 59`), so each sits mid-cell,
  half a 2 h step off the hour labels.
- **§6.2** The icon box is `24 x 24`. This is a ceiling, not a preference: the
  2 h step is `24.47` px, so a 24 px box clears its neighbour by `0.47` px at
  every width. Anything larger collides.
- **§6.3** Vertical rule: the icon's bottom sits `5` px above the curve's
  highest point across the icon's own 24 px of width, clamped to the plot top.
  yr's own placement is not a function of the curve and is not recoverable
  (measured gaps 3.2-8.1 px, median 5.3), so this is a stated rule that matches
  yr where the curve is flat and lands within ~3 px elsewhere. §5.1 reserves
  `24 + 5 = 29` px of headroom for it, so the clamp only fires when the ladder
  runs out.
- **§6.4** Icons are drawn last, over the bars and the curve, as in the
  original: a tall rain bar can run through one. The icon of a 2 h cell is the
  `symbol_code` of the sample at the cell's **first** hour (point index `i - 1`).
- **§6.5** Icon art is theme-independent: one set, identical in light and dark
  (§9.3).
- **§6.6** No icon label, no icon border. The set itself is out of this spec:
  83 codes, 7 vector and 76 wrapping the app's raster art, extracted in the
  previous map's ticket 03.

## §7 Precipitation

- **§7.1** Precipitation is drawn in the same band as the temperature, on its
  own right-hand axis - there is no second panel. Bars are drawn behind the
  curve and behind the icons.
- **§7.2** The mm axis is **fixed** `0 .. 10 mm/h`, `12` px per mm, `0` at the
  band bottom. It never rescales with the data: two renders an hour apart are
  directly comparable, and an empty row states "no rain" without a word.
- **§7.3** One bar per hourly **interval**, so 60 bars for 61 points: the bar of
  point `i` covers `[i, i+1)` and carries that sample's rate, which is what
  met.no's `next_1_hours` means. The last point contributes no bar.
- **§7.4** Bar geometry (§0): `x = i * step + 0.5`, width `11.2373`,
  `y = 120 - 12 * mm`, down to the band bottom. Flat fill, full opacity, no
  stroke, no rounding.
- **§7.5** A rate above 10 mm/h is clipped to the band top and its value is
  printed on the bar: whole millimetres (`13`, not `13.4`), `12` px, in the
  over-max colour, `text-anchor="middle"` on the bar's centre at
  `y = 6 dy="0.35em"`. Two digits are 15.3 px of ink on an 11.2373 px bar, so a
  rect in the **bar colour**, the label's width plus 2 px either side, is
  painted behind the text: without it the overflow lands on the background
  whenever the neighbouring hours are dry and the digits lose their edges.
- **§7.6** A sample with no precipitation value draws no bar, exactly like a
  `0.0`. The distinction is not drawn anywhere on the image.
- **§7.7** Sub-zero temperatures do not turn precipitation into snow here: the
  rate is drawn as precipitation, whatever the temperature reads. The icon
  carries the phase.
- **§7.8** Bars are discrete; nothing interpolates between them and nothing
  smooths their tops. Precipitation is intermittent, and a smoothed edge would
  draw rain in hours that were never forecast.

## §8 Stale data

- **§8.1** Stale means the last successful fetch is more than `6` hours old.
- **§8.2** A stale image shows the last good graph, unchanged, plus an age chip
  in the legend row, right-aligned to the plot's right edge: `94 x 18` at
  `(670.2373, 186)`, rounded `3`, filled with the background at 85 %, a `6` px
  dot at `+8, +9`, and the age at `+16`, baseline `+13`, `12` px, muted colour.
  The text reads `vor 7 h` (hours, rounded, up to 48; then `vor 3 Tagen`). The
  chip is 94 px because `vor 3 Tagen` is 71.9 px of ink at 12 px. The legend row
  is empty right of `x = 254`, so the chip can never collide with anything -
  which the old top-right corner could not promise, now that the top right is
  the day-label row.
- **§8.3** No cache at all (first run, nothing ever fetched) draws the whole
  frame - background, cell grid, day separators, both axes, hour and day
  labels, legend - because every one of those is derivable from the clock
  alone. The °C axis falls back to a `0 .. 30` band (`r = 3`). Over the plot,
  centred, the text `noch keine Daten` at `16` px, with the last error text at
  `12` px under it. No curve, no bars, no icons, no fake data.
- **§8.4** Neither the age chip nor the empty state changes the layout: no
  element moves, no row resizes, the canvas is the same.

## §9 Not shown

Explicit omissions, so no ticket adds them back by accident:

- **§9.1** Wind: no wind band, no arrows, no speed, gust, or direction. This is
  the point of the widget.
- **§9.2** Attribution and header: no yr logo, no NRK or met.no credit, no
  location line, no title row. A private, unpublished widget; the operator
  ruled this out. It is also why publishing the widget stays out of scope.
- **§9.3** Moon phases are not drawn, and yr's dark-mode moon tint is not
  cloned: 76 of our 83 icons wrap raster art that cannot be recoloured, and a
  night set that is half-tinted reads worse than one that is not tinted at all.
  Night is carried by the icon art only.
- **§9.4** No "now" marker, no mouse-over values, no tooltips, no interaction of
  any kind: it is an image.
- **§9.5** A `lang` option: labels are German. A handful of strings do not earn an
  option yet; add it when a second language is actually needed.
- **§9.6** Any effect: shadows, glows, rounded plot corners. The curve's
  gradient (§5.5) is a colour switch, not a decoration, and the §4.9 shade
  carries information (the hours of daylight); nothing else is a gradient.
- **§9.7** `kein Niederschlag`. An empty rain row on a fixed axis (§7.2)
  already says it, and the text cost a clause that had to be kept in step with
  the scale.

## §10 Defaults

| Clause | Default |
| --- | --- |
| §1.1 width | `794` (clamped to `560..1588`) |
| §1.1 height | `round(W * 210 / 794.2373)` |
| §1.1 artifact | SVG; PNG is the same geometry rasterised |
| §2.1 theme | `light` |
| §3.1 canvas | `794.2373 x 210` |
| §3.2 plot | `734.2373 x 120` at `x = 30` |
| §4.1 window | `60 h`, step `1 h`, `61` points, from the render hour |
| §4.3 cell grid | vertical every hour, horizontal every `12` px |
| §4.6 day-label threshold | `6.5` h |
| §4.7 hour labels | every `2 h`, last point skipped (`30` labels) |
| §4.9 day/night shade | on, `1 h` ramp either side of each sun event |
| §5.1 °C ladder | `r` in `1, 2, 3, 5, 10` °C per row, 10 rows, `29` px headroom |
| §6.1 icon cadence | every `2 h` (`30` icons) |
| §6.2 icon box | `24 x 24` |
| §7.2 mm axis | fixed `0..10 mm/h`, `12` px per mm |
| §8.1 stale threshold | `6 h` |
| §8.3 fallback °C band | `0..30` (`r = 3`) |

## Retired clauses

The rewrite kept §1-§10 as sections and held a clause number wherever the
clause survived (§1.2, §1.4, §2.1-§2.3, §3.4, §4.1, §4.2, §5.7, §8.1-§8.4, §9.1,
§9.2, §9.5). These numbers no longer mean what an older citation in the code expects,
and are not reused for anything related:

| Old | Was | Now |
| --- | --- | --- |
| §1.3 | fixed 2:1 aspect | aspect follows the design canvas (§1.3, rewritten) |
| §1.5 | "the PNG is the only published artifact" | the SVG is (§1.1) |
| §3.1 | page padding `8` | gone; gutters and row stack (§3.1-§3.3) |
| §3.2 | left label gutter `36`, content `44..774` | plot and two `30` px gutters (§3.2) |
| §3.3 | two stacked panels | one band (§3.3) |
| §3.5 | one right-aligned axis column | two-sided axis labelling (§3.5) |
| §4.3 | vertical grid every `6 h` | full cell grid (§4.3) |
| §4.4 | thick midnight line carrying the weekday | separator (§4.4) and day label (§4.5) split |
| §4.5 | two-letter weekday names | numeric German day labels (§4.5) |
| §4.6 | "no hour numbers, no clock times" | hour labels every 2 h (§4.7) |
| §5.1 | 5 °C step fit with a reserved step | ladder fit, 10 rows (§5.1) |
| §5.4 | a grid line per 5 °C step, each labelled | §4.3 grid, §3.5 labels |
| §5.5 | 0 °C drawn as a distinct zero line | the curve gradient splits there instead (§5.5) |
| §5.6 | unit on the topmost label, `15 °C` | unit on the axis glyph (§5.6) |
| §6.1-§6.3 | `28 px` icons every `3 h`, `3` px clearance | `24 px` every `2 h`, `5` px (§6.1-§6.3) |
| §7.1-§7.5 | a second panel, rescaling mm/h axis, `55 %` filled area | fixed axis, bars in the one band (§7.1-§7.5) |
| §7.6 | `kein Niederschlag` when dry | dropped (§9.7) |
| §8.2 | age chip `70 x 18` at `(704, 8)` | `94 x 18` in the legend row, same clause number (§8.2) |
| §8.3 | empty state: frame and text only | full chrome, same clause number (§8.3) |
| §9.4 | no hour numbers, no clock times, no now marker | now marker and interaction only (§9.4) |
