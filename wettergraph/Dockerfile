# Pinned, not floating: the floating tag was re-pointed without changing the
# version label, so "latest" and "3.24-2026.08.0" resolve to the same digest
# today and need not tomorrow.
FROM ghcr.io/home-assistant/base:3.24-2026.08.0

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

# python3 for the service; curl for the container self-test; font-dejavu for
# the renderer, which loads DejaVuSans.ttf by file path (graph-spec §3.4) -
# resvg draws no text at all when no font answers, with no error.
# uv comes from Alpine community as a static binary - no pip, no venv tooling,
# no interpreter-auto-download for this project itself.
RUN apk add --no-cache \
        python3 \
        curl \
        tzdata \
        ca-certificates \
        font-dejavu \
        uv

# uv refuses to run without a project in the working directory, so point the
# wrapper at the one the image carries.
RUN printf '#!/bin/sh\nexec /usr/bin/uv --project /app "$@"\n' > /usr/local/bin/WGUV \
    && chmod +x /usr/local/bin/WGUV

WORKDIR /app
COPY app/ /app/

# The renderer needs resvg-py, pinned in uv.lock. It is a Rust extension, but
# musllinux wheels exist for both target arches, so no compiler is needed here;
# uv pulls the wheel into /app/.venv (system Python, no managed download).
RUN uv sync --frozen --no-cache

# The container's payload. Without a CMD the base image's s6 has nothing to
# supervise and the container exits as soon as stage2 finishes. The venv's
# python is the one with resvg-py in it.
CMD [ "/app/.venv/bin/python", "/app/server.py" ]
