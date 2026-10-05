# travel-ops

Search live flight and stay offers through a command line interface or an MCP-compatible agent. The engine
normalizes and compares offers, keeps prices in their original currencies, and reports each source's status and
any limits on the results. It is read-only: it does not book, check out, create accounts, or handle traveler or
payment details.

## Install

Install [uv](https://docs.astral.sh/uv/) and Google Chrome (Booking.com hands its anti-bot token only to a real
Chrome; the other sources do not need it), then run:

```bash
./install.sh
```

The installer creates `profile.yml` from `profile.example.yml` if one is not present. The personal profile and
the `data/` directory are excluded from Git.

## Use from the CLI

```bash
uv run travelops flights IST MOW 2026-11-14
uv run travelops flights MOW LED 2026-11-14 --flex 1 --sources aviasales,tutu
uv run travelops stays Lisbon 2026-11-14 2026-11-16
uv run travelops sources
uv run travelops doctor
```

Use `travelops --help` for all options. The screen shows the 15 cheapest cards and says how many exist; `--limit N`
changes that and `--json` prints everything. Long searches may ask for confirmation before they start. Source limits,
previous refusals, missing results, and truncated coverage are reported explicitly. A result from one seller says
nothing about another seller that was not searched.

## Use with an agent

The MCP server runs over standard input/output:

```bash
uv run travelops mcp
```

The repository includes `.codex/config.toml` for Codex CLI and IDE, and `.mcp.json` for MCP clients that support
that manifest format. Codex loads project-local configuration only after the repository is trusted. Other agents
that support MCP stdio can register the same command (`uv`, arguments `run travelops mcp`) in their own MCP
configuration. `AGENTS.md` contains shared operating instructions; `CLAUDE.md` imports those instructions for
Claude Code. Client configuration formats differ, but all clients call the same MCP server and deterministic
search engine.

The MCP tools are `search_flights`, `search_stays`, and `sources`. The `sources` tool is read-only and does not
contact travel sites. The search tools return the cheapest cards (`limit`, 10 by default) with `shown` telling how many
exist, plus exact source statuses, timestamps, links, original currencies, conversion-rate dates, and narrowing
details for the agent to present.

## Sources

| Source | Search | Access method |
| --- | --- | --- |
| Aviasales | Flights | Browser session and HTTP requests; prices of every selling agency |
| Tutu | Flights | Read-only Tutu MCP search |
| OneTwoTrip | Flights | HTTP requests |
| Kupibilet | Flights | HTTP requests |
| Wildberries Travel | Flights | Browser session and HTTP requests |
| Booking.com | Stays | Browser session and HTTP requests |
| Airbnb | Stays | HTTP requests |

Sites may limit automated requests or block them. Respect each site's terms and use the tool for personal-volume
searches. Do not bypass blocks with proxies or use booking, checkout, or passenger-data flows.

## Development

Offline tests are the default and do not send requests to travel sites:

```bash
uv run pytest -q
```

Live source checks consume persistent per-source request budgets and should be run only when explicitly intended.
Never run them in a loop or concurrently. See [NOTICE](NOTICE) for project attributions and currency-rate
attribution.
