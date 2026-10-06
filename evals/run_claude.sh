#!/usr/bin/env bash
# Example: drive one scenario with the Claude Code CLI, then check it. Needs `claude` logged in and `yq`.
#   evals/run_claude.sh evals/scenarios/kotor-weekend /tmp/run
set -euo pipefail
scenario=$(cd "$1" && pwd)
run=$2
root=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$run"
context=$(yq -r '.context' "$scenario/scenario.yml")
turns=$(yq -r '.turns | length' "$scenario/scenario.yml")
session=$(uuidgen | tr '[:upper:]' '[:lower:]')
cd "$root"
for ((i = 1; i <= turns; i++)); do
  say=$(yq -r ".turns[$((i - 1))].say" "$scenario/scenario.yml")
  later=$(yq -r ".turns[$((i - 1))].minutes_later // 0" "$scenario/scenario.yml")
  cat >"$run/mcp.json" <<JSON
{"mcpServers": {"travel-ops": {"command": "uv", "args": ["run", "travelops", "mcp"],
  "env": {"TRAVELOPS_REPLAY": "$scenario", "TRAVELOPS_TRACE": "$run/turn-$i.trace.jsonl",
          "TRAVELOPS_REPLAY_MINUTES": "$later"}}}}
JSON
  if ((i == 1)); then conversation=(--session-id "$session"); else conversation=(--resume "$session"); fi
  claude -p "$say" "${conversation[@]}" --append-system-prompt "$context" \
    --mcp-config "$run/mcp.json" --strict-mcp-config --allowedTools "mcp__travel-ops" "Read" \
    >"$run/turn-$i.md"
done
uv run travelops eval "$scenario" "$run"
