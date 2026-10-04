"""The Wettergraph app service.

It proves the app starts, that Supervisor's options reach us at
/data/options.json, and, since ticket 04, that the met.no data side runs: a
daemon thread polls ``locationforecast/2.0/compact`` when ``Expires`` allows,
caches the last good series under ``/data``, and keeps serving it when met.no
is unreachable.

Since ticket 05 the served image is the real graph: ``render.render_view``
turns the cached series into the PNG that ``docs/graph-spec.md`` describes,
with ``?width=`` and ``?theme=light|dark`` as the two per-request knobs. The
intermediate SVG stays available at ``/image/graph.svg`` for debugging.

The page reads its values from the options file on every request, so editing an
option in the UI changes the page with no rebuild.

Ticket 06 is delivery. The same PNG is what Home Assistant's Generic Camera
polls on the app's own port, ``update_interval``, ``image_width`` and
``image_theme`` decide what is served, the response is explicitly uncacheable,
and a copy is kept in ``/share`` for the case where the HA instance cannot
reach the port.

Ticket 07 is the *dashboard* route, because a browser-loaded card cannot use
the port at all: the dashboard is HTTPS, the port is HTTP, and the image is
blocked as mixed content. So both themes are also written into HA's own ``www``
folder and served by HA at ``/local/wettergraph/graph-light.svg`` and
``graph-dark.svg`` - same origin, no rotating token, scalable in its tile.

The night is shaded (graph-spec §4.9) from HA's own ``sun.sun`` entity, read
through the Supervisor API (sun.py). ``day_night`` switches it, ``?daynight=``
overrides the option per request.
"""

from __future__ import annotations

import html
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import localzone
import metno
import publish
import render
import sun

OPTIONS_PATH = Path(os.environ.get("WG_OPTIONS", "/data/options.json"))
DATA_DIR = Path("/data")
STARTED_AT = time.time()
# (zone, source) once main() has resolved it; see localzone.py.
ZONE = ("UTC", "not resolved yet")

# Ticket 06, cache-busting. HA's own camera view (/api/camera_proxy) sends no
# cache headers at all, so the browser's guarantee is the camera access token,
# which HA rotates every 5 minutes. This header makes the intent explicit for
# anything that reads the endpoint directly, and never costs a stale frame.
NO_CACHE = "no-store, no-cache, must-revalidate, max-age=0"

# Set in main() (or in --self-test mode); the request handler reads it.
CACHE: metno.ForecastCache | None = None
# HA's sun.sun, refreshed in main(); without it the graph has no shading.
SUN = sun.SunReader()
# The /share copy of the served image (the fallback route, see publish.py).
PUBLISHER = publish.Publisher()
# Ticket 07: the dashboard's own artifact, on the HA origin under /local/. Both
# themes are written unconditionally, so a card can switch on sun.sun without
# the app's `image_theme` option having anything to say about it.
WWW = {
    theme: publish.Publisher(
        publish.DEFAULT_WWW_DIR,
        filename=f"graph-{theme}.svg",
        label=f"www {theme}",
        # `www` itself may not exist on a fresh HA and is ours to create; the
        # mount above it is Supervisor's, and its absence means "not mapped".
        mount=publish.DEFAULT_WWW_DIR.parent,
    )
    for theme in ("light", "dark")
}
# What HA serves the files above as; `www` is HA's config dir, `/local/` the URL.
WWW_URL_BASE = f"/local/{publish.SUBDIR}"


def options() -> dict:
    """Read /data/options.json fresh, so UI edits show up without a rebuild."""
    try:
        with OPTIONS_PATH.open() as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        return {"_error": f"invalid JSON: {exc}"}


def cache_for(opts: dict) -> metno.ForecastCache:
    """Build the met.no client from the app options, with defaults."""
    place_id, lat, lon, interval = place_from(opts)
    return metno.ForecastCache(
        place_id=place_id,
        lat=lat,
        lon=lon,
        update_interval_minutes=interval,
    )


def place_from(opts: dict) -> tuple[str, float, float, int]:
    """Options -> (place_id, lat, lon, update_interval), never raises."""

    def number(name: str, default: float) -> float:
        try:
            return float(opts.get(name, default))
        except (TypeError, ValueError):
            return float(default)

    return (
        str(opts.get("place_id") or metno.PLACE_ID_DEFAULT),
        number("latitude", metno.LAT_DEFAULT),
        number("longitude", metno.LON_DEFAULT),
        int(number("update_interval", 15)),
    )


def reconfigure(cache: metno.ForecastCache) -> None:
    """Re-read the options before each poll: place and cadence change live."""
    place_id, lat, lon, interval = place_from(options())
    cache.reconfigure(
        place_id=place_id, lat=lat, lon=lon, update_interval_minutes=interval
    )


def image_options(opts: dict) -> tuple[int, str]:
    """Options -> (width, theme) for a request that asks for nothing.

    This is what "the options decide the served dimensions" means: the URL's
    ``?width=`` / ``?theme=`` still win per request (§1.2, §2.2), but the app's
    own default - the status page's image, the /share copy - follows the
    options. Out-of-range or junk is clamped, never refused, exactly as a query
    parameter is (both go through the renderer's own clamp).
    """
    width = render.clamp_width(opts.get("image_width", render.DEFAULT_WIDTH))
    theme = str(opts.get("image_theme") or "").strip().lower()
    return width, (theme if theme in render.PALETTES else "light")


def day_night(opts: dict, query: dict | None = None) -> bool:
    """§4.9 shading on or off: ``?daynight=`` wins, else the option (default on)."""
    if query and "daynight" in query:
        return flag(query, "daynight")
    return opts.get("day_night", True) is not False


def sun_for(opts: dict, query: dict | None = None) -> dict | None:
    """The sun.sun reading to render with, or None for no shading."""
    return SUN.reading() if day_night(opts, query) else None


def default_png(now: float | None = None) -> bytes:
    """The image served when the URL carries no knobs: options + cached series."""
    opts = options()
    width, theme = image_options(opts)
    return render.render_view(
        current_view(), width=width, theme=theme, now=now or time.time(), sun=sun_for(opts)
    )


def theme_svg(theme: str, now: float | None = None) -> bytes:
    """The SVG ticket 07 publishes for one theme: options for width, no age chip.

    The chip is off for the same reason it is off the /share PNG - it moves
    every minute and would rewrite the file every minute with it. Width still
    follows ``image_width`` because it sets the SVG's intrinsic size, which is
    what a card with ``rows: auto`` scales *from*.
    """
    opts = options()
    width, _ = image_options(opts)
    svg = render.build_view_svg(
        current_view(), width=width, theme=theme, now=now or time.time(), sun=sun_for(opts)
    )
    return svg.encode("utf-8")


def flag(query: dict, name: str) -> bool:
    """?age, ?age=1, ?age=true, ?age=yes... all switch a debug option on."""
    values = query.get(name)
    return bool(values) and str(values[0]).strip().lower() not in (
        "", "0", "false", "no", "off",
    )


def age_text(seconds: float | None) -> str:
    """"never" / "4 min" / "2.5 h" - the status page's only time format."""
    if seconds is None:
        return "never"
    if seconds < 3600:
        return f"{int(seconds) // 60} min"
    return f"{seconds / 3600:.1f} h"


def current_view() -> dict:
    """What the renderer and the status page read, even before the data layer
    has anything (then it is the graph-spec §8.3 no-cache case)."""
    if CACHE is not None:
        return CACHE.view()
    return {
        "place": {},
        "samples": [],
        "fetched_at": None,
        "age_seconds": None,
        "stale": False,
        "expires_at": None,
        "last_outcome": "data layer not started",
        "last_error": "data layer not started",
        "cache_path": "-",
    }


def data_html() -> str:
    """The data-layer line on the status page."""
    if CACHE is None:
        return "<p>forecast data: data layer not started</p>"
    view = current_view()
    state = "STALE" if view["stale"] else ("fresh" if view["samples"] else "empty")
    return (
        f"<p>forecast data: <b>{len(view['samples'])}</b> samples, last fetch {age_text(view['age_seconds'])} ago "
        f"({state}), outcome <code>{html.escape(view['last_outcome'])}</code>, "
        f"last error <code>{html.escape(view['last_error'] or 'none')}</code></p>"
        f"<p>time zone: <b>{html.escape(ZONE[0])}</b> (from <code>{html.escape(ZONE[1])}</code>), "
        f"local time now {time.strftime('%H:%M %Z')}</p>"
        f"<p>met.no cache: <code>{html.escape(view['cache_path'])}</code> - raw view at "
        f"<code>/forecast.json</code></p>"
        f"{sun_html()}"
    )


def sun_html() -> str:
    """The day/night line on the status page."""
    if not day_night(options()):
        return "<p>day/night shading: off (option <code>day_night</code>)</p>"
    state = SUN.status()
    reading = state["reading"]
    if reading is None:
        return (
            f"<p>day/night shading: <b>no sun.sun yet</b>, last error "
            f"<code>{html.escape(state['last_error'] or 'none')}</code></p>"
        )
    return (
        f"<p>day/night shading: sun.sun <code>{html.escape(str(reading['state']))}</code>, "
        f"next rising <code>{html.escape(str(reading['next_rising']))}</code>, next setting "
        f"<code>{html.escape(str(reading['next_setting']))}</code>, read "
        f"{age_text(state['age_seconds'])} ago</p>"
    )


def www_html() -> str:
    """Ticket 07: the state of the two /local/ SVGs, and the card that reads them."""
    lines = []
    for theme, pub in WWW.items():
        state = pub.status()
        if state["last_error"]:
            lines.append(
                f"<p>{theme} SVG: <code>{html.escape(state['path'])}</code> - "
                f"<b>not writable</b>: <code>{html.escape(state['last_error'])}</code>. "
                f"The app needs <code>map: - homeassistant_config:rw</code>; if it has it, "
                f"update to this version so Supervisor recreates the container with the "
                f"mapping.</p>"
            )
        elif state["exists"]:
            lines.append(
                f"<p>{theme} SVG: <code>{html.escape(state['path'])}</code> - "
                f"{state['bytes']} B, written {age_text(state['age_seconds'])} ago "
                f"({state['writes']} write(s) since this start), served by HA at "
                f"<code>{WWW_URL_BASE}/graph-{theme}.svg</code>.</p>"
            )
        else:
            lines.append(
                f"<p>{theme} SVG: <code>{html.escape(state['path'])}</code> - not on disk yet.</p>"
            )
    return "".join(lines)


def dashboard_html(host: str | None, opts: dict) -> str:
    """The copy-paste block for the dashboard card, plus the camera routes.

    ``host`` is this request's Host header, so a page opened straight on the
    app's own port can print the exact URL to paste. Behind HA ingress the host
    is Home Assistant's, not the app's, and the camera has to dial the app
    directly: then only the template is printed.
    """
    width, theme = image_options(opts)
    port = int(os.environ.get("WG_PORT", "8099"))
    query = f"image/graph?width={width}&theme={theme}"
    template = f"http://&lt;HA-host&gt;:{port}/{query}"
    names = host or ""
    exact = (
        f"http://{html.escape(names)}/{query}"
        if names and names.rsplit(":", 1)[-1] == str(port)
        else None
    )
    where = (
        f"<p>Use this URL, copied from the address bar of the page you are on: "
        f"<br><code>{exact}</code></p>"
        if exact
        else f"<p>This page is served through ingress, so it cannot see the address the "
        f"camera has to dial. Use the app's own port (8099, unless you changed it "
        f"under Network) with the address you reach Home Assistant on: <br><code>{template}</code></p>"
    )
    interval = int(place_from(opts)[3])
    share = PUBLISHER.status()
    if share["last_error"]:
        share_line = (
            f"<p>shared copy: <code>{html.escape(share['path'])}</code> - "
            f"<b>not writable</b>: <code>{html.escape(share['last_error'])}</code>. "
            f"The app needs <code>map: - share:rw</code> (it has it: reinstall or update "
            f"to this version to pick the mapping up).</p>"
        )
    elif share["exists"]:
        share_line = (
            f"<p>shared copy: <code>{html.escape(share['path'])}</code> - "
            f"{share['bytes']} B, written {age_text(share['age_seconds'])} ago, in step "
            f"with <code>/image/graph</code> ({share['writes']} write(s) since this "
            f"start).</p>"
        )
    else:
        share_line = f"<p>shared copy: <code>{html.escape(share['path'])}</code> - not on disk yet.</p>"
    return f"""<h2>Dashboard: the card that keeps itself current</h2>
<p>Home Assistant serves its own <code>www</code> folder at <code>/local/</code>, and this
app writes both themes there, so the card reads a <b>same-origin, non-rotating</b>
URL. This is the route to use: the app's own port is HTTP, the dashboard is
HTTPS, and a browser blocks that image as mixed content whatever it contains.</p>
<pre>type: custom:refreshable-picture-card
refresh_interval: 600
url: {WWW_URL_BASE}/graph-light.svg
attribute: ''
noMargin: true
tap_action:
  action: more-info
grid_options:
  rows: auto
  columns: 18</pre>
<p>The card appends its own <code>?currentTimeCache=</code> on every refresh, which is
what beats HA's 31-day cache header on <code>/local/</code>; no version query of ours is
needed. Wrap two of these in a <code>conditional</code> on <code>sun.sun</code> to swap
<code>graph-light.svg</code> for <code>graph-dark.svg</code> after dark.</p>
{www_html()}
<h3>The camera routes (unchanged)</h3>
<p>Settings -&gt; Devices &amp; services -&gt; <b>Add integration</b> -&gt; <b>Generic Camera</b>.
Leave <i>Stream Source</i> empty, paste this into <i>Still Image URL</i>, and confirm
the preview:</p>
{where}
<p>Leave the advanced section at its defaults. <i>Frame rate</i> is only a floor for
repeated fetches of the same URL, not a refresh schedule, so it does not set the
cadence; the frame is picked up when HA asks for it.</p>
<p>Cadence: the data changes on met.no's schedule (<code>Expires</code>, floored by
<code>update_interval</code> = {interval} min); HA re-reads this image on its own about
every 5 minutes, without anyone touching the dashboard. Append <code>&amp;age=1</code> to
the URL to draw the data age on the image - the number moves, which is how you
watch a card refresh (off by default: graph-spec §9.4 keeps the clock off it).</p>
<h3>If Home Assistant cannot reach port {port}</h3>
<p>Then nothing on the dashboard can fetch from this app, and the file copy is the
delivery: add a <b>Local file</b> camera instead (same Add integration dialog) and
give it the file path below, which is the same file this app writes every time
the graph changes.</p>
<p>File path: <code>{html.escape(share['path'])}</code></p>
{share_line}
"""


def page_html(opts: dict, host: str | None = None) -> str:
    rows = "".join(
        f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in sorted(opts.items())
    )
    return f"""<!doctype html>
<meta charset="utf-8">
<title>Wettergraph</title>
<style>
  body {{ font: 16px/1.5 DejaVu Sans, sans-serif; background: #1c1c1e; color: #f2f2f7; margin: 2rem; }}
  h1 {{ font-size: 1.4rem; }}
  h2 {{ font-size: 1.15rem; margin-top: 2rem; }}
  h3 {{ font-size: 1rem; margin-top: 1.5rem; }}
  table {{ border-collapse: collapse; margin-top: 1rem; }}
  th, td {{ text-align: left; padding: .3rem .8rem; border-bottom: 1px solid #3a3a3c; }}
  th {{ color: #9aa7b4; font-weight: normal; }}
  p {{ max-width: 62rem; }}
  pre {{ background: #14161a; padding: .6rem .8rem; border-radius: 6px; overflow-x: auto; }}
  img {{ margin-top: 1.5rem; border-radius: 8px; }}
  code {{ color: #8fd6ff; }}
</style>
<h1>Wettergraph</h1>
<p>Rendering is live: the image below is the cached met.no series drawn to
<code>docs/graph-spec.md</code>. Temperature curve, weather icons, precipitation
band; no wind. <code>?width=</code> (560-1588), <code>?theme=dark</code> and
<code>?daynight=0|1</code> are per-request; without them the options decide.</p>
<p><code>{OPTIONS_PATH}</code> - read fresh on every request.</p>
<table>{rows}</table>
{data_html()}
{dashboard_html(host, opts)}
<!-- Relative URLs: the page is also served behind HA ingress, which strips
     /api/hassio_ingress/<token> before forwarding. A root-absolute path would
     resolve against the Home Assistant origin there and 404. -->
<img src="image/graph" alt="Wettergraph forecast" width="{image_options(opts)[0]}">
<p><a href="image/graph.svg">intermediate SVG</a> - <a href="image/graph?theme=dark">dark variant</a> - <a href="forecast.json">forecast.json</a></p>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "wettergraph/0.1"

    def _send(self, code: int, body: bytes, ctype: str, cache: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        opts = options()
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            page = page_html(opts, self.headers.get("Host"))
            self._send(200, page.encode(), "text/html; charset=utf-8", "no-store")
            return

        if path == "/health":
            self._send(200, b"ok\n", "text/plain; charset=utf-8", "no-store")
            return

        if path == "/forecast.json":
            self._send(
                200,
                json.dumps(current_view(), indent=2).encode(),
                "application/json; charset=utf-8",
                "no-store",
            )
            return

        # The PNG is the Generic Camera's copy of the published SVG (graph-spec
        # §1.1): HA fetches this one server-side, so no browser sees it. Width
        # and theme are clamped and validated inside the renderer (§1.2, §2.2);
        # without them the options decide. ?age=1 forces the §8.2 chip on fresh
        # data.
        if path in ("/image/graph", "/image/graph.png"):
            view = current_view()
            width, theme = image_options(opts)
            png = render.render_view(
                view,
                width=query.get("width", [""])[0] or width,
                theme=query.get("theme", [""])[0] or theme,
                now=time.time(),
                show_age=flag(query, "age"),
                sun=sun_for(opts, query),
            )
            age = view.get("age_seconds")
            self._send(
                200,
                png,
                "image/png",
                NO_CACHE,
                {
                    "Pragma": "no-cache",
                    # For a human debugging a card: curl -sD- .../image/graph | head
                    "X-Wettergraph-Age": "-" if age is None else str(int(age)),
                },
            )
            return

        # The intermediate SVG stays inside the app: same box, debug route.
        if path == "/image/graph.svg":
            width, theme = image_options(opts)
            svg = render.build_view_svg(
                current_view(),
                width=query.get("width", [""])[0] or width,
                theme=query.get("theme", [""])[0] or theme,
                now=time.time(),
                show_age=flag(query, "age"),
                sun=sun_for(opts, query),
            )
            self._send(200, svg.encode("utf-8"), "image/svg+xml; charset=utf-8", "no-store")
            return

        self._send(404, b"not found\n", "text/plain; charset=utf-8", "no-store")

    def log_message(self, fmt: str, *args) -> None:
        print(f"wettergraph: {self.address_string()} {fmt % args}", flush=True)


def run_checks(bind_port: int = 0) -> list[tuple[str, bool, str]]:
    """Exercise every route against a throwaway in-process server.

    bind_port 0 asks the OS for a free port, which is what the startup run
    wants: it must not collide with the port the real server is about to take.
    """
    import threading
    import urllib.error
    import urllib.request

    httpd = ThreadingHTTPServer(("127.0.0.1", bind_port), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def fetch(path: str):
        """(status, content-type, body bytes); never raises."""
        try:
            with urllib.request.urlopen(base + path, timeout=20) as resp:
                return resp.status, resp.headers.get("Content-Type", ""), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type", ""), exc.read()
        except OSError as exc:
            return 0, "", f"unreachable: {exc}".encode()

    def fetch_headers(path: str) -> tuple[int, dict]:
        """(status, headers) for a GET whose body is thrown away."""
        try:
            with urllib.request.urlopen(base + path, timeout=20) as resp:
                return resp.status, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers)
        except OSError as exc:
            return 0, {"error": str(exc)}

    opts = options()
    opt_width, opt_theme = image_options(opts)
    checks: list[tuple[str, bool, str]] = []

    status, _, body = fetch("/health")
    checks.append(("/health returns 200 ok", status == 200 and body.strip() == b"ok", f"{status} {body[:40]!r}"))

    status, ctype, body = fetch("/")
    page = body.decode(errors="replace")
    rows = page.count("<tr>")
    # Root-absolute links (/image/graph) resolve against the HA origin when the
    # page is opened through ingress, not against this server.
    relative_links = 'src="/' not in page and 'href="/' not in page
    checks.append(
        (
            "/ serves an HTML page",
            status == 200 and "text/html" in ctype and relative_links,
            f"{status} {ctype}"
            + ("" if relative_links else " - root-absolute links break behind ingress"),
        )
    )
    checks.append(
        (
            "page lists every option",
            bool(opts) and rows == len(opts),
            f"{rows} rows for {len(opts)} options" + (" (options.json missing or unreadable)" if not opts else ""),
        )
    )
    if opts and "_error" not in opts:
        key, value = sorted(opts.items())[0]
        checks.append(("option values reach the page", str(value) in page, f"{key}={value!r}"))
    checks.append(
        (
            "the page carries the Generic Camera URL and the file fallback",
            "Generic Camera" in page
            and "image/graph?width=" in page
            and "Local file" in page
            and f"{WWW_URL_BASE}/graph-light.svg" in page,
            f"{len(page)}B page",
        )
    )

    status, ctype, body = fetch("/image/graph")
    width = int.from_bytes(body[16:20], "big") if len(body) >= 24 else 0
    height = int.from_bytes(body[20:24], "big") if len(body) >= 24 else 0
    checks.append(
        (
            f"/image/graph serves a PNG at the option width ({opt_width}x{render.height_for(opt_width)})",
            status == 200
            and "png" in ctype
            and body[:8] == b"\x89PNG\r\n\x1a\n"
            and (width, height) == (opt_width, render.height_for(opt_width)),
            f"{status} {ctype} {len(body)}B {width}x{height}",
        )
    )

    status, headers = fetch_headers("/image/graph")
    checks.append(
        (
            "the image is served uncacheable and reports its data age",
            status == 200
            and "no-store" in headers.get("Cache-Control", "")
            and headers.get("X-Wettergraph-Age") is not None,
            f"{headers.get('Cache-Control')!r} age={headers.get('X-Wettergraph-Age')!r}",
        )
    )

    status, ctype, body = fetch("/image/graph?width=560&theme=dark")
    width = int.from_bytes(body[16:20], "big") if len(body) >= 24 else 0
    checks.append(
        (
            "/image/graph honours ?width (clamped) and ?theme over the options",
            status == 200 and width == 560,
            f"{status} {width}px against the option's {opt_width}px ({opt_theme})",
        )
    )

    status, ctype, body = fetch("/image/graph.svg")
    svg = body.decode(errors="replace")
    checks.append(
        (
            "/image/graph.svg serves the intermediate SVG, no wind",
            status == 200 and "svg" in ctype and svg.startswith("<svg") and "wind" not in svg.lower(),
            f"{status} {ctype} {len(svg)}B",
        )
    )
    checks.append(("render font is readable (graph-spec §3.4)", render.FONT_PATH.is_file(), str(render.FONT_PATH)))

    try:
        now = time.time()
        fixture = render.fixture_samples(now)
        fixture_svg = render.build_svg(fixture, fetched_at=now, now=now)
        icons_drawn = fixture_svg.count("scale(0.24)")
        ok = (
            'stroke="url(#temperature-curve-gradient)"' in fixture_svg
            and "Niederschlag mm" in fixture_svg
            and icons_drawn == 30
        )
        detail = f"{len(fixture_svg)}B SVG, {icons_drawn} icons"
    except Exception as exc:  # noqa: BLE001 - a broken renderer must be reported, not fatal
        ok, detail = False, f"renderer raised {exc!r}"
    checks.append(("renderer draws the curve, 30 icons and the bars (fixture)", ok, detail))

    # Ticket 06's one visible freshness signal: the §8.2 chip, forced on fresh
    # data by ?age=1, with minutes under an hour so it visibly moves.
    try:
        fresh_chip = render.build_svg(
            render.fixture_samples(now)[:60], fetched_at=now - 300, now=now, show_age=True
        )
        ok = ">vor 5 min<" in fresh_chip and "0.85" in fresh_chip
        detail = "chip reads 'vor 5 min' 5 min after the fetch" if ok else "no minute chip in the SVG"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"renderer raised {exc!r}"
    checks.append(("?age=1 draws the age chip (the card's only moving pixel)", ok, detail))

    # §4.9: the renderer shades a fixture night, and HA's sun.sun is readable.
    try:
        shaded = render.build_svg(fixture, fetched_at=now, now=now, sun=sun.fixture(now))
        ok = 'fill="url(#day-night-gradient)"' in shaded
        detail = "fixture night shaded" if ok else "no day/night gradient in the SVG"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"renderer raised {exc!r}"
    checks.append(("renderer shades the night (fixture, graph-spec §4.9)", ok, detail))
    if day_night(opts):
        state = SUN.status()
        checks.append(
            (
                "sun.sun is readable for the day/night shading",
                state["reading"] is not None,
                str(state["reading"]["state"]) if state["reading"] else f"{state['last_error']}",
            )
        )
    else:
        checks.append(("sun.sun is readable for the day/night shading", True, "day_night is off"))

    # The file copy has to be in step, not freshly written: after a restart the
    # file is usually already correct, and "writes" would then be 0 forever.
    served = default_png()
    PUBLISHER.publish(served)
    shared = PUBLISHER.status()
    try:
        on_disk = Path(shared["path"]).read_bytes()
    except OSError:
        on_disk = b""
    checks.append(
        (
            f"the fallback copy is in step ({shared['path']})",
            on_disk == served,
            f"{len(on_disk)}B on disk, {len(served)}B served, {shared['writes']} write(s) this run"
            + (f", {shared['last_error']}" if shared["last_error"] else ""),
        )
    )

    # Ticket 07: the dashboard reads these two, so "in step" is the whole point.
    for theme, pub in WWW.items():
        wanted = theme_svg(theme)
        pub.publish(wanted)
        state = pub.status()
        try:
            on_disk = Path(state["path"]).read_bytes()
        except OSError:
            on_disk = b""
        checks.append(
            (
                f"the {theme} dashboard SVG is in step ({WWW_URL_BASE}/graph-{theme}.svg)",
                on_disk == wanted,
                f"{len(on_disk)}B on disk, {len(wanted)}B rendered, "
                f"{state['writes']} write(s) this run"
                + (f", {state['last_error']}" if state["last_error"] else ""),
            )
        )

    status, _, _ = fetch("/definitely-not-a-route")
    checks.append(("unknown paths 404", status == 404, str(status)))
    checks.append(("options file is readable", OPTIONS_PATH.exists(), str(OPTIONS_PATH)))

    status, ctype, body = fetch("/forecast.json")
    ok = status == 200 and "application/json" in ctype
    detail = f"{status} {ctype}"
    if ok:
        try:
            doc = json.loads(body)
            needed = {
                "place", "samples", "fetched_at", "age_seconds", "stale",
                "last_outcome", "last_error", "cache_path",
            }
            missing = sorted(needed - set(doc))
            ok = not missing
            detail = f"{status} {len(doc.get('samples', []))} samples" + (
                f", missing keys: {missing}" if missing else ""
            )
        except json.JSONDecodeError as exc:
            ok = False
            detail = f"{status} invalid JSON: {exc}"
    checks.append(("/forecast.json serves the normalised series", ok, detail))
    checks.append(
        (
            "met.no User-Agent is descriptive",
            metno.USER_AGENT.startswith("Wettergraph/") and "+http" in metno.USER_AGENT,
            metno.USER_AGENT,
        )
    )

    checks.append(
        (
            "the time axis has a real local zone (graph-spec §4.1)",
            not ZONE[1].startswith("fallback"),
            f"{ZONE[0]} from {ZONE[1]}, now {time.strftime('%H:%M %Z')}",
        )
    )

    httpd.shutdown()
    return checks


def report(checks: list[tuple[str, bool, str]]) -> int:
    failed = 0
    for name, ok, detail in checks:
        print(f"wettergraph: {'PASS' if ok else 'FAIL'}  {name}  ({detail})", flush=True)
        failed += 0 if ok else 1
    print(f"wettergraph: startup check {len(checks) - failed}/{len(checks)} passed", flush=True)
    return failed


def main() -> None:
    global CACHE, ZONE
    # First, before anything renders: the hour labels are drawn in local time.
    ZONE = localzone.apply()
    port = int(os.environ.get("WG_PORT", "8099"))
    print(
        f"wettergraph: starting on :{port}; options={OPTIONS_PATH} "
        f"present={OPTIONS_PATH.exists()} build={os.environ.get('BUILD_VERSION', 'dev')}",
        flush=True,
    )
    CACHE = cache_for(options())
    # First read in the foreground, so the first published files are shaded.
    SUN.fetch_once()
    print(f"wettergraph: time zone {ZONE[0]} (from {ZONE[1]}), local time now {time.strftime('%H:%M %Z')}", flush=True)
    print(f"wettergraph: met.no UA={metno.USER_AGENT!r} cache={CACHE.cache_path()}", flush=True)
    # Every file exists before anyone asks for it, so a Local file camera or a
    # /local/ card added later has something to show (publish.py).
    print(f"wettergraph: share target {PUBLISHER.path}", flush=True)
    PUBLISHER.publish(default_png())
    for theme, pub in WWW.items():
        print(f"wettergraph: www target {pub.path} -> {WWW_URL_BASE}/graph-{theme}.svg", flush=True)
        pub.publish(theme_svg(theme))
    if not publish.DEFAULT_WWW_DIR.parent.is_dir():
        print(
            f"wettergraph: WARNING {publish.DEFAULT_WWW_DIR.parent} is not mapped, so the "
            "dashboard SVGs cannot be written; add `homeassistant_config:rw` and update",
            flush=True,
        )
    elif not publish.DEFAULT_WWW_DIR.is_dir():
        print(
            f"wettergraph: NOTE created {publish.DEFAULT_WWW_DIR}; Home Assistant registers "
            "/local/ at startup, so restart Home Assistant once or the SVGs 404",
            flush=True,
        )
    if render.FONT_PATH.is_file():
        print(f"wettergraph: render font {render.FONT_PATH} present, icons {render.ICONS_DIR}", flush=True)
    else:
        print(
            f"wettergraph: ERROR render font {render.FONT_PATH} is not readable; "
            "resvg draws no text at all (graph-spec §3.4)",
            flush=True,
        )
    stop = threading.Event()
    threading.Thread(target=SUN.run_forever, args=(stop,), name="sun-reader", daemon=True).start()
    threading.Thread(
        target=CACHE.run_forever,
        args=(stop,),
        kwargs={"reconfigure": reconfigure},
        name="metno-poller",
        daemon=True,
    ).start()
    # Keeps every published file in step with what the endpoint serves: render
    # every minute, write only on a change. One thread, so the PNG and the two
    # SVGs share a cadence.
    threading.Thread(
        target=publish.run_forever,
        args=(
            [
                (PUBLISHER, default_png),
                (WWW["light"], lambda: theme_svg("light")),
                (WWW["dark"], lambda: theme_svg("dark")),
            ],
            stop,
        ),
        name="publisher",
        daemon=True,
    ).start()
    # Report into the app log, where the operator can actually read it: HAOS
    # gives no shell inside an app container.
    report(run_checks())
    try:
        ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
    finally:
        stop.set()


if __name__ == "__main__":
    if "--self-test" in os.sys.argv:
        CACHE = cache_for(options())
        CACHE.load()
        SUN.fetch_once()
        os.sys.exit(1 if report(run_checks(int(os.environ.get("WG_PORT", "8099")))) else 0)
    main()
