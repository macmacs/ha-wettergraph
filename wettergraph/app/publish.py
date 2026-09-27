#!/usr/bin/env python3
"""Publish the graph as files Home Assistant itself can read (tickets 06, 07).

Two destinations, one mechanism.

``/share/wettergraph/graph.png`` is ticket 06's fallback: HA core sees it as
``/share/wettergraph/graph.png``, so a **Local file** camera shows the same
graph with no HTTP in between, for the case where HA cannot reach this app's
port. The app may write there because ``config.yaml`` maps ``share:rw``.

``/homeassistant/www/wettergraph/graph-<theme>.svg`` is ticket 07's dashboard
route, and it exists because port 8099 is unusable for a *browser-loaded* card:
the dashboard is HTTPS, the port is HTTP, and the image is blocked as mixed
content (ticket 00). HA serves its own ``www`` folder at ``/local/``, so the
file lands on the HA origin at a path that does not rotate the way an ingress
token does. That folder is HA's config dir, which an app only sees when
``config.yaml`` maps ``homeassistant_config:rw`` - mounted at
``/homeassistant``, since Supervisor mounts every map type at ``/<type-name>``.

Writes are change-driven. The loop renders every ``interval`` seconds but only
writes when the bytes differ, so the common case costs one render and no disk
write. Renders are byte-identical for the same input (``render.py``), which is
what makes that comparison meaningful.

A destination that is missing or read-only is reported **once**, in the app log
and on the status page, not every minute: on a box without the mapping that
route is simply unavailable, and the others keep working.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

DEFAULT_SHARE_DIR = Path(os.environ.get("WG_SHARE", "/share"))
# HA's own config dir, mapped by `homeassistant_config:rw`. Supervisor mounts
# every map type at /<type-name>, so this is /homeassistant - not /config,
# which inside an app container is the app's *own* config folder.
DEFAULT_WWW_DIR = Path(os.environ.get("WG_WWW", "/homeassistant/www"))
SUBDIR = "wettergraph"
FILENAME = "graph.png"


class Publisher:
    """Keep one file in step with the served image. Never raises."""

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        subdir: str = SUBDIR,
        filename: str = FILENAME,
        label: str = "share",
        mount: Path | str | None = None,
    ) -> None:
        self.root = Path(root) if root is not None else DEFAULT_SHARE_DIR
        self.path = self.root / subdir / filename
        # The directory Supervisor bind-mounts. Without this guard an unmapped
        # app would happily `mkdir -p` the whole path *inside its own
        # container*, write there, and report success while Home Assistant sees
        # nothing at all - the worst failure available, because it looks fine.
        self.mount = Path(mount) if mount is not None else self.root
        # What the log line and the status page call this destination, so two
        # publishers reporting the same OSError stay tellable apart.
        self.label = label
        self.writes = 0
        self.last_write: float | None = None
        self.last_error: str | None = None
        self._reported: str | None = None

    # ------------------------------------------------------------------ state

    def status(self) -> dict:
        """The file's own state, plus what this run has written.

        The age comes from the file's mtime, not from this process: after a
        restart the copy is usually already current, and "written 3 minutes ago"
        should then describe the file, not the restart.
        """
        try:
            info = self.path.stat()
            exists, size, mtime = True, info.st_size, info.st_mtime
        except OSError:
            exists, size, mtime = False, 0, None
        return {
            "path": str(self.path),
            "exists": exists,
            "bytes": size,
            "mtime": mtime,
            "age_seconds": None if mtime is None else max(0.0, time.time() - mtime),
            "writes": self.writes,
            "last_error": self.last_error,
        }

    def _note(self, message: str) -> None:
        """Log a problem the first time it happens; stay quiet afterwards."""
        self.last_error = message
        if message != self._reported:
            print(f"wettergraph: {self.label} {message}", flush=True)
            self._reported = message

    # ---------------------------------------------------------------- writing

    def publish(self, payload: bytes) -> bool:
        """Write ``payload`` when the file on disk differs. True when a write happened."""
        if not self.mount.is_dir():
            self._note(f"{self.mount} is not mapped into this app; {self.path} not written")
            return False

        try:
            if self.path.read_bytes() == payload:
                self.last_error = None
                return False
        except OSError:
            pass  # missing (the normal first run) or unreadable: try to write

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Write beside the target and rename: a reader must never see half a
            # file. HA may read it at any moment - a camera polling the PNG, or
            # a browser fetching the SVG off /local/.
            scratch = self.path.with_name(self.path.name + ".tmp")
            scratch.write_bytes(payload)
            scratch.replace(self.path)
        except OSError as exc:
            self._note(f"cannot write {self.path}: {type(exc).__name__}: {exc}")
            return False

        self.writes += 1
        self.last_write = time.time()
        if self.last_error is not None:
            print(f"wettergraph: {self.label} recovered, writing {self.path}", flush=True)
            self.last_error = None
        self._reported = None
        print(
            f"wettergraph: {self.label} wrote {self.path} "
            f"({len(payload)}B, write #{self.writes})",
            flush=True,
        )
        return True


def run_forever(jobs, stop, interval: float = 60.0) -> None:
    """Publish every job immediately, then whenever its render changes.

    ``jobs`` is a sequence of ``(publisher, render)`` pairs; each ``render()``
    returns the bytes that publisher should hold. The caller keeps the options
    and the cached series, so this module stays unaware of both. One thread
    drives every destination, so the PNG and the SVGs share a cadence and a
    failing render on one never stalls the others.
    """
    while True:
        for publisher, render in jobs:
            try:
                publisher.publish(render())
            except Exception as exc:  # noqa: BLE001 - a broken render must not kill the loop
                publisher._note(f"render failed: {type(exc).__name__}: {exc}")
        if stop.wait(interval):
            return


if __name__ == "__main__":  # pragma: no cover - manual probe on a dev box
    import sys

    if len(sys.argv) != 2:
        sys.exit("usage: publish.py <png>   # copies a file into the share folder")
    source = Path(sys.argv[1])
    publisher = Publisher()
    changed = publisher.publish(source.read_bytes())
    print(f"{'wrote' if changed else 'unchanged'}: {publisher.path}")
    print(publisher.status())
