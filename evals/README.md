# Agent checks

The engine has unit tests. These check the other half: what an agent does with `AGENTS.md`, `modes/` and the
tools. A scenario is a conversation over a search that really happened, recorded once and replayed offline, so
a check costs no request to any travel site and gives the same data every time.

## What is checked

Mechanically, with no model as a judge:

- **Calls.** The turn needed these tools with these arguments; it must not search where a view of an earlier
  search answers; a tool error counts.
- **Prices.** Every amount of money in the answer is in a tool result of this conversation, or is the sum or the
  difference of two that are. An estimate or a rounded price fails.
- **Links.** Every link in the answer is in a tool result, character for character.
- **Reports.** Every source that was asked is named in the answer.
- **Form.** The language of the human, no tables, and the facts the scenario lists (`must_match`).

What it cannot tell: whether the advice is good. Read the answers for that.

## Run

Start the agent with the MCP server in replay mode. Two environment variables do it:

- `TRAVELOPS_REPLAY=evals/scenarios/<name>`: answers come from the recording, the network is closed, the clock
  stands at the moment of the recording;
- `TRAVELOPS_TRACE=<run>/turn-N.trace.jsonl`: the server writes every call and its answer there.

A turn with `minutes_later: N` in the scenario happens that long after the recording: set
`TRAVELOPS_REPLAY_MINUTES=N` for that turn, and the prices the agent sees are N minutes old.

Give the agent the scenario's `context` and say turn N; save its reply as `<run>/turn-N.md`; then:

```bash
uv run travelops eval evals/scenarios/kotor-weekend <run>
```

`run_claude.sh` does this with the Claude Code CLI. It is an example for one agent, not part of the check:
any agent that speaks MCP can be driven the same way.

## Record a scenario

Search live the usual way, then within half an hour copy what memory holds:

```bash
uv run python scripts/eval_record.py evals/scenarios/<name> trip BEG Kotor 2026-10-22 2026-10-23 --country Montenegro
```

It writes `memory/` (the searches, cut to the cheapest cards), `raw.json` (the geocoder and the rates) and a
`scenario.yml` to fill in. Opaque booking links are replaced by stand-ins. Look through the files before a
commit: they are public.
