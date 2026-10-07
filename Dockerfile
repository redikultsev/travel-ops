# travel-ops in a container: the MCP server over HTTP (the default command) or the price watches
# (`travelops watch run --every-minutes 30`), from one image. Booking hands its anti-bot token only to a real
# Chrome; Aviasales and Wildberries open in Camoufox, which is a Firefox. Both browsers and their system libraries
# are here; Playwright's own Chromium is not, nothing uses it.
# The profile is mounted at /app/profile.yml, the data (searches, watches, request limits) at /data.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm@sha256:85d4cb1afa769a7338e095b927bee941cf5ec92266c7424b3f6c0f2748567248

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
ENV PATH=/app/.venv/bin:$PATH
# Both browsers are pinned: an image rebuilt next month has the same browsers until the pins are moved on
# purpose (README, «Run in a container»: rebuild monthly with --no-cache and new pins).
# Google Chrome stable from Google's repository, checked against the SHA256 its signed index lists for it.
ARG CHROME_VERSION=155.0.8059.39-1
ARG CHROME_SHA256=c58aa0f2cd66179c9f050e062c882d27aa9b9f8c2b7c73fee3498560b5ed0b38
# Camoufox's Firefox: one release of the official channel, not whatever is newest at build time.
ARG CAMOUFOX_VERSION=official/stable/156.0.1-beta.34
# As root: the libraries Firefox needs, and Google Chrome with its own.
RUN playwright install-deps firefox \
    && curl -fsSL -o /tmp/chrome.deb \
       "https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_${CHROME_VERSION}_amd64.deb" \
    && echo "${CHROME_SHA256}  /tmp/chrome.deb" | sha256sum -c - \
    && apt-get update && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm -rf /tmp/chrome.deb /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 travel && install -d -o travel -g travel /data
USER travel
# Camoufox's Firefox and its GeoIP base, into the user's cache; the build log says which one is active.
RUN python -m camoufox set ${CAMOUFOX_VERSION} && python -m camoufox active
ENV TRAVELOPS_DATA=/data
EXPOSE 8765
CMD ["travelops", "mcp", "--http", "--host", "0.0.0.0", "--port", "8765"]
