#!/usr/bin/env bash
set -euo pipefail

command -v uv >/dev/null || {
  echo "Install uv first: https://docs.astral.sh/uv/" >&2
  exit 1
}

uv sync
uv run playwright install chromium
uv run python -m camoufox fetch

if [[ ! -f profile.yml ]]; then
  cp profile.example.yml profile.yml
fi

uv run travelops doctor
echo "Ready. Run 'uv run travelops --help' or connect an MCP-compatible agent."
