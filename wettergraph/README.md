# Wettergraph (Home Assistant app)

The widget. It starts, polls met.no's `locationforecast/2.0/compact` on the
schedule met.no itself asks for, caches the last good forecast under `/data`,
and serves a status page, the normalised forecast as JSON, and the graph itself
as a PNG: temperature curve, weather icons, precipitation band, no wind
anywhere. The dashboard card reads the graph as an **SVG on the HA origin**,
`/local/wettergraph/graph-{light,dark}.svg`, because a browser-loaded card
cannot fetch the app's own HTTP port from an HTTPS dashboard. The Generic
Camera polling the PNG and the `/share` copy both stay as the camera routes.

## Install

Home Assistant OS only (native container installs have no app store).

1. Settings -> Add-ons -> Add-on Store -> three-dot menu top right ->
   **Repositories**.
2. Add `https://github.com/macmacs/ha-wettergraph` (the repo root holds
   `repository.yaml`; the app itself is in `wettergraph/`).
3. Reload the store page if needed, then install **Wettergraph**.
4. **Start** it and open the **Log** tab. First lines:

   `wettergraph: starting on :8099; options=/data/options.json present=True build=0.5.1`
   `wettergraph: time zone Europe/Berlin (from /homeassistant/.storage/core.config), local time now 12:36 CEST` (or `from TZ`)
   `wettergraph: met.no UA='Wettergraph/0.5.1 (Home Assistant app; +https://github.com/macmacs/ha-wettergraph)' cache=/data/forecast-cache.json`
   `wettergraph: share target /share/wettergraph/graph.png`
   `wettergraph: www target /homeassistant/www/wettergraph/graph-light.svg -> /local/wettergraph/graph-light.svg`
   `wettergraph: www target /homeassistant/www/wettergraph/graph-dark.svg -> /local/wettergraph/graph-dark.svg`
   `wettergraph: render font /usr/share/fonts/dejavu/DejaVuSans.ttf present, icons /app/icons`

   Then the poller's first line - `metno: 200 OK, 88 samples cached to
   /data/forecast-cache.json`, or a named error instead (`403 Forbidden`,
   `429 Too Many Requests`, `unreachable`) - and a startup check that exercises
   the real routes in-process (HAOS gives you no shell inside the container, so
   this is the self-test):

   ```
   wettergraph: PASS  /health returns 200 ok  (200 b'ok\n')
   wettergraph: PASS  / serves an HTML page  (200 text/html; charset=utf-8)
   wettergraph: PASS  page lists every option  (7 rows for 7 options)
   wettergraph: PASS  option values reach the page  (image_theme='light')
   wettergraph: PASS  the page carries the Generic Camera URL and the file fallback  (3597B page)
   wettergraph: PASS  /image/graph serves a PNG at the option width (794x210)  (200 image/png 45943B 794x210)
   wettergraph: PASS  the image is served uncacheable and reports its data age  ('no-store, no-cache, must-revalidate, max-age=0' age='121')
   wettergraph: PASS  /image/graph honours ?width (clamped) and ?theme over the options  (200 560px against the option's 794px (light))
   wettergraph: PASS  /image/graph.svg serves the intermediate SVG, no wind  (200 image/svg+xml; charset=utf-8 36769B)
   wettergraph: PASS  render font is readable (graph-spec §3.4)  (/usr/share/fonts/dejavu/DejaVuSans.ttf)
   wettergraph: PASS  renderer draws the curve, 30 icons and the bars (fixture)  (74638B SVG, 30 icons)
   wettergraph: PASS  ?age=1 draws the age chip (the card's only moving pixel)  (chip reads 'vor 5 min' 5 min after the fetch)
   wettergraph: PASS  the fallback copy is in step (/share/wettergraph/graph.png)  (24875B on disk, 24875B served, 0 write(s) this run)
   wettergraph: PASS  the light dashboard SVG is in step (/local/wettergraph/graph-light.svg)  (36769B on disk, 36769B rendered, 0 write(s) this run)
   wettergraph: PASS  the dark dashboard SVG is in step (/local/wettergraph/graph-dark.svg)  (36774B on disk, 36774B rendered, 0 write(s) this run)
   wettergraph: PASS  unknown paths 404  (404)
   wettergraph: PASS  options file is readable  (/data/options.json)
   wettergraph: PASS  /forecast.json serves the normalised series  (200 88 samples)
   wettergraph: PASS  met.no User-Agent is descriptive  (Wettergraph/0.5.1 (Home Assistant app; +https://github.com/macmacs/ha-wettergraph))
   wettergraph: PASS  the time axis has a real local zone (graph-spec §4.1)  (Europe/Berlin from TZ, now 12:36 CEST)
   wettergraph: startup check 20/20 passed
   ```

   The byte counts and the sample count are yours; the paths and the check
   names are the same everywhere. `the fallback copy is in step` FAILing means
   `/share` is not mapped into the container (or is not writable) - the
   dashboard path does not use it, but the Local file camera fallback (see
   **The dashboard**) then has nothing to read.

   `the light/dark dashboard SVG is in step` FAILing with `/homeassistant is
   not mapped into this app` means `homeassistant_config:rw` has not reached
   the container: update the app (Supervisor recreates the container with the
   new mapping) or, failing that, uninstall and reinstall. The app deliberately
   does **not** create `/homeassistant` itself - it would then be writing into
   a folder only the container can see, and everything would look fine while
   Home Assistant served nothing.

   `render font is readable` FAILing means the image will render with every
   text node missing (resvg draws nothing when no font answers) - report it.

   `present=False`, or `options file is readable` showing FAIL, means options
   never reached the container - report that, it is the interesting failure.
   The same check can be re-run from the HA terminal with
   `docker exec $(docker ps -q -f name=wettergraph | head -1) /app/.venv/bin/python /app/server.py --self-test`.
5. Open the **Wettergraph** panel in the sidebar (or the "Open web UI" button).
   You should see the status page: a table of the options, one `forecast data:`
   line (samples, age, stale, last error), the cache path, and at the bottom
   **the graph**. The sample count should be 80-90 within a minute of starting;
   0 with a `last error` set means the fetch failed, and the error text says why.
   Until the first fetch lands the graph is the `noch keine Daten` frame, not a
   fake curve.
6. Change `page_note` under **Configuration**, save, then reload the page.
   The new value must appear **without** restarting the app. Use this to prove
   options are re-read live.

The sidebar entry is opt-in: on the add-on page, use **Open web UI**, or enable
**Add to sidebar**. If neither shows the page, the app is still installed and
running, and `/health` on port 8099 is what the watchdog uses - say so instead of
fighting ingress; ingress is not on the critical path.

## Forecast data (ticket 04)

One daemon thread inside the app is the only thing that talks to met.no. It
fetches `https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=&lon=`
(4 decimals, the precision met.no caches on), normalises the response, and
writes it to `/data/forecast-cache.json`.

- **User-Agent**, required by met.no:
  `Wettergraph/0.5.1 (Home Assistant app; +https://github.com/macmacs/ha-wettergraph)`
  (`BUILD_VERSION`, so a version bump changes it).
- **Polling follows met.no's `Expires` header**: the next request is not sent
  before it. Once it has passed, the request carries `If-Modified-Since`, and a
  `304 Not Modified` means the series is still current (still counted as a
  successful fetch).
- **No API key.** `locationforecast/2.0/compact` is open.
- **The cache survives a restart.** After a restart the log says
  `metno: restored 88 samples ...`, and no request goes out while `Expires` still
  holds.
- **A failed fetch keeps the last good series.** The app never shows an empty
  graph because the network blinked; after 6 h without a successful fetch the
  series counts as stale and the rendered image will carry the age chip
  (`docs/graph-spec.md` §8.1, §8.2).
- **`403` and `429` are named in the log** and backed off (60 s, doubling to
  30 min; `Retry-After` is honoured). The poller cannot crash-loop: errors are
  caught and the app keeps serving.

Where to look:

- status page - the `forecast data:` line (samples, age, state, last error)
- `http://<ha-host>:8099/forecast.json` - the raw view: `samples`, `fetched_at`,
  `age_seconds`, `stale`, `last_error`, `expires_at`, `cache_path`
- log - one `metno:` line per poll, roughly every 30-60 min, `304 Not Modified`
  when the model has not changed
- log - one `share wrote ...` / `www light wrote ...` line each time a
  published file changes (so
  after startup and after each new forecast)

The series shape the renderer consumes, one entry per met.no timeseries entry:

```json
{"time": "2026-09-22T18:00:00Z",     "temperature": 11.7,
 "precipitation": 0.0,                  "symbol_code": "clearsky_night"}
```

`temperature` is °C and `precipitation` is mm/h; there is no unit conversion
anywhere (`docs/graph-spec.md` §5.7 is Celsius only). `precipitation` comes
from the finest hook the entry carries: `next_1_hours` as is, `next_6_hours` and
`next_12_hours` divided back to an hourly rate. `symbol_code` is met.no's own
code (`<condition>_<timeofday>`), so day/night art follows from the string.

Expected measurements:

- Container memory: a few tens of MB (python3 + resvg, no browser).
- Startup to a served page: a few seconds.
- One render: ~140 ms at 794 px with 30 icons on the dev box (61 ms of that
  is the SVG, the rest resvg), roughly twice the retired 16-icon layout.
  Still cheap for a camera polled every few minutes; the dashboard card
  reads the published SVG and costs nothing per view.
- `curl http://<ha-host>:8099/health` -> `ok`, if the port is reachable from
  where you run curl. The sidebar UI works over ingress regardless.

## The graph (ticket 05)

The published artifact is an **SVG**, written to HA's own `www/wettergraph/`
and served at `/local/` (redesign tickets 00 and 07). `GET /image/graph.svg`
serves the same file; `GET /image/graph` rasterises it for the Generic Camera,
which HA fetches server-side. The graph is a **clone of yr's meteogram without
the wind band and without the yr/NRK header**, drawn to `docs/graph-spec.md`
clause for clause. Two per-request knobs, both validated by the renderer:

- `?width=` - the intrinsic size, `width x round(width * 210/794.2373)`,
  clamped to 560..1588 (`docs/graph-spec.md` §1.2); junk falls back to 794.
  The card scales the SVG to its tile, so this mostly sets the PNG's size.
- `?theme=light|dark` - anything unknown is light (§2.2).

What the image holds: a 60 h window from the hour of the **render**, its tail
interpolated out of met.no's 6-hourly entries (§4.8); yr's cell grid with a
day separator at every local midnight; a Catmull-Rom temperature curve (2 px)
whose red/blue split is one gradient at 0 °C, on a band fitted by the §5.1
ladder (10 rows, 1/2/3/5/10 °C a row); hourly rain bars on a **fixed** 0..10
mm/h axis; a °C axis left and an mm axis right, each with yr's own glyph;
hour labels every 2 h; German numeric day labels; one 24 px icon every 2 h from
`wettergraph/app/icons/` (83 MET codes, chosen by `symbol_code`); and yr's
legend row. No wind of any kind, no attribution text - both are on purpose.

Degrading honestly: over 6 h since the last successful fetch the last good
graph is served plus the age chip (`vor 7 h`, `vor 3 Tagen`); with no cache at
all the frame plus `noch keine Daten` and the last error.

- Renderer: `wettergraph/app/render.py` - Python + `resvg-py`, no browser, no
  headless Chrome.
- Font: DejaVu Sans (`font-dejavu` in the image) loaded **by file path**, per
  spec §3.4; resvg draws no text at all when no font answers, so the startup
  check asserts the file is readable. Text nodes name the whole stack
  `DejaVu Sans, Verdana, sans-serif`: resvg stops at the first name and the PNG
  is unchanged, while a browser handed `/image/graph.svg` directly - where the
  file path means nothing - falls through to a font the viewer actually has.
- The intermediate SVG stays at `/image/graph.svg` (same knobs) for debugging;
  the PNG is the published artifact.
- Preview renders: `docs/preview-light.svg` and `docs/preview-dark.svg`, a
  live Oslo forecast at 794 px, one per theme. `tools/render-check.py` proves
  all 91 clause checks, including geometry parity against
  `tools/assets/graph-reference-v2.svg`.

## The dashboard (ticket 07)

**The card cannot use port 8099.** The dashboard is served over HTTPS and the
app's port is HTTP, so a browser blocks the image as mixed content - PNG or
SVG, it never arrives. (Typing the URL into a tab works: that is a top-level
navigation, which the rule does not cover.) The Generic Camera below is
unaffected only because *HA* fetches it server-side and re-serves it on the HA
origin.

So the app writes both themes into Home Assistant's own `www` folder, which HA
serves at `/local/`:

    /homeassistant/www/wettergraph/graph-light.svg   ->  /local/wettergraph/graph-light.svg
    /homeassistant/www/wettergraph/graph-dark.svg    ->  /local/wettergraph/graph-dark.svg

`map: - homeassistant_config:rw` in `config.yaml` is what makes that folder
writable. Note the mount point: Supervisor mounts every map type at
`/<type-name>`, so HA's config dir is `/homeassistant`. `/config` inside an app
container is the app's *own* public config folder, not HA's.

The card is HACS's `custom:refreshable-picture-card`:

    type: custom:refreshable-picture-card
    refresh_interval: 600
    url: /local/wettergraph/graph-light.svg
    attribute: ''
    noMargin: true
    tap_action:
      action: more-info
    grid_options:
      rows: auto
      columns: 18

`rows: auto` lets the SVG scale to its tile. The card appends its own
`?currentTimeCache=<ms>` on every refresh, which is what beats HA's 31-day
`Cache-Control` on `/local/`; the app does not need to version the URL. Wrap
two of these in a `conditional` on `sun.sun` to swap light for dark.

Two things to know about that folder:

- **An SVG in `<img>` loads no external CSS**, so the *browser* picks the font,
  not the renderer's pinned DejaVu. Every text node names
  `DejaVu Sans, Verdana, sans-serif` (`render.FONT_STACK`) for that reason;
  resvg stops at the first name, so the PNG is unaffected.
- **If `www` did not exist before**, Home Assistant registers `/local/` at
  startup and will 404 until it is restarted once. The app log says so when it
  creates the folder.

### The camera routes (ticket 06)

Home Assistant's own **Generic Camera**: no HACS, no custom card, nothing to
keep updated. It fetches `http://<ha-host>:8099/image/graph`, and HA re-reads
that image on its own schedule. Still the route for a **Picture entity** card
or anything that wants a `camera.` entity.

1. Settings -> Devices & services -> **Add integration** -> **Generic Camera**.
2. **Still Image URL**: `http://<ha-host>:8099/image/graph?width=794&theme=light`
   (`<ha-host>` is the address you use for Home Assistant; `/image/graph` alone
   works too and then follows the app's options). Leave **Stream Source** empty.
3. Leave the advanced section at its defaults. **The `frame_interval` option
   this integration used to have is gone**: what is left is *Frame rate*, and
   that is only a floor for repeated fetches of the same URL
   (`frame_interval = 1 / frame rate`, default 2). It does not set the refresh
   schedule, so there is nothing to copy `update_interval` into.
4. The dialog fetches the URL and shows a preview, so a wrong host or port
   fails right there rather than silently on the dashboard. Then add it to a
   card (**Picture entity** or **Picture glance**), or use the camera entity
   `camera.wettergraph` anywhere.

**Where the cadence comes from.** met.no decides when the data changes: the
app does not poll before its `Expires`, so new data lands every ~30-60 min, with
`update_interval` as the floor. Home Assistant then re-reads the image by
itself about every 5 minutes - the camera access token in
`/api/camera_proxy/camera.wettergraph?token=...` rotates on that interval, which
changes the URL the dashboard asks for. Nothing on the way caches: the endpoint
answers `Cache-Control: no-store, no-cache, must-revalidate, max-age=0` plus
`Pragma: no-cache`, and a fresh frame is therefore what the card draws.

**How to watch it refresh.** Add `&age=1` to the URL in the camera config. That
draws the age chip graph-spec §8.2 reserves for stale data, now on fresh data,
with minutes instead of hours under an hour (`vor 4 min`). The number moves, so
a wall-mounted dashboard proves by itself that it is re-reading the image.
Without the switch the image carries no clock at all (§9.4).

**Diagnostics.**

- `curl -sD- -o/dev/null http://<ha-host>:8099/image/graph | head` - the
  response headers, including `X-Wettergraph-Age` (seconds since the last
  successful met.no fetch) and the cache headers above.
- The app log prints one line per request the camera makes, from HA's own IP:
  `wettergraph: <ha-ip> "GET /image/graph?width=794&theme=light HTTP/1.1" 200`.
  A line every ~5 minutes is the dashboard refreshing itself.
- `http://<ha-host>:8099/` shows the URL to paste, the cadence it is running
  with, and the state of the `/share` copy.

**Fallback: when Home Assistant cannot reach port 8099 either.** Some installs (port
conflict, firewall, HA in its own Docker network) cannot dial the app. HA can
still read a file, so the app writes the same PNG - same options, same render -
to:

    /share/wettergraph/graph.png

`map: - share:rw` in `config.yaml` is what makes that folder writable; the file
is rewritten only when the picture actually changes, so a quiet hour costs one
render a minute and no disk writes. To use it, add a **Local file** camera
(same *Add integration* dialog) with that path as its *File path*. HA core reads
the file directly, so no port, no HTTP, and nothing to reach.

The file copy carries no age chip: `&age=1` is a URL switch and a file has no
URL. Its freshness is the `shared copy: ... written 4 min ago` line on the
status page (served through ingress, so it is still readable on exactly the
install where the port is blocked) and the `share wrote ...` line in the log.

## Configuration

| Option | Default | Meaning |
| --- | --- | --- |
| `page_note` | `setup probe` | Free text, shown on the status page. Proves live option reads. |
| `place_id` | `2-6325496` | met.no/yr place id, Olympia Tower by default. |
| `latitude` | `48.1746` | 4 decimals on purpose: met.no caches on ~4 decimals. |
| `longitude` | `11.5538` | Same reason. |
| `update_interval` | `15` | Minutes between refreshes. Used when met.no serves no `Expires` header; `Expires` wins when present. |
| `image_width` | `794` | Width of the image the app serves when the URL asks for nothing, clamped to 560..1588 (graph-spec §1.2). Height follows the design canvas, `round(width * 210/794.2373)`. |
| `image_theme` | `light` | `light` or `dark` when the URL asks for nothing (§2.2). |

The last two are the **defaults**: the status page's own image, the `/share`
copy and any bare `/image/graph` follow them, while `?width=` and `?theme=` in a
URL still win per request, so one dashboard can be dark at 1044 px while another
is light at 794. Change either under **Configuration** and the next request uses
it - no rebuild, nothing to reinstall.

## How it is built

- Base image `ghcr.io/home-assistant/base:3.24-2026.08.0` (Alpine 3.24, s6
  overlay init, bashio), pinned rather than floating.
- `python3`, `curl`, `tzdata`, `font-dejavu`, `ca-certificates` and `uv` from
  Alpine apk. No pip, no virtualenv tooling, no interpreter download.
- `resvg-py` (the renderer's Rust extension) is installed at build time by
  `uv sync --frozen` from the committed `wettergraph/app/uv.lock` into
  `/app/.venv`; the container's `CMD` is that venv's python
  (`/app/.venv/bin/python`). musllinux wheels exist for both target arches, so
  no compiler is needed.
- `init: false` in `config.yaml` is **required**, not optional: the base image's
  s6 must be PID 1. Supervisor's `init` defaults to true, which puts Docker's
  tini in as PID 1 and makes s6 exit with
  `s6-overlay-suexec: fatal: can only run as pid 1`.
- The Dockerfile needs a `CMD` for the same reason inverted: without one, s6 has
  nothing to supervise and the container exits as soon as it finishes starting.

## Local checks

    uv run --with pyyaml --with voluptuous python tools/addon-lint.py
    uv run --with resvg-py --with pillow python tools/render-check.py   # every graph-spec clause
    uv sync --project wettergraph/app       # resvg-py into wettergraph/app/.venv

    # the renderer alone; --now makes a fixture render reproducible
    uv run --project wettergraph/app python wettergraph/app/render.py --dry --now 1758456000 --out /tmp/dry.png
    uv run --project wettergraph/app python wettergraph/app/render.py --cache /tmp/wg/forecast-cache.json --out /tmp/sample.png

    # the whole app; needs an options file, and WG_FONT on a box with no DejaVu
    # at the container's path, WG_SHARE/WG_WWW where there is no /share or
    # /homeassistant (this dev box has none of them). WG_WWW's *parent* has to
    # exist: an absent mount is how the app knows it is not mapped, and it
    # refuses to write rather than fill a folder only the container can see.
    mkdir -p /tmp/wg/share /tmp/wg/homeassistant
    WG_OPTIONS=/tmp/options.json WG_FONT=/tmp/DejaVuSans.ttf WG_DATA=/tmp/wg \
    WG_SHARE=/tmp/wg/share WG_WWW=/tmp/wg/homeassistant/www \
      uv run --project wettergraph/app python wettergraph/app/server.py --self-test
    WG_OPTIONS=/tmp/options.json WG_FONT=/tmp/DejaVuSans.ttf WG_DATA=/tmp/wg \
    WG_SHARE=/tmp/wg/share WG_WWW=/tmp/wg/homeassistant/www \
      uv run --project wettergraph/app python wettergraph/app/server.py   # then http://localhost:8099/

    python3 wettergraph/app/metno.py --cache-dir /tmp/wg --once   # one real fetch
    python3 wettergraph/app/metno.py --cache-dir /tmp/wg --watch  # poll until Ctrl-C

`tools/addon-lint.py` re-runs Supervisor's own store scan (glob `**/config.*`
outside dot-dirs and `rootfs`, validate against the same voluptuous schema) so a
misnamed or malformed config is caught before you install anything.
