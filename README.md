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

On a bare Linux server the browsers also need system libraries, and Chrome comes as a package. Both need root:

```bash
uv run playwright install-deps firefox
uv run playwright install --with-deps chrome
```

`uv run travelops doctor` starts both browsers and says what is missing.

The installer creates `profile.yml` from `profile.example.yml` if one is not present. The personal profile and
the `data/` directory are excluded from Git.

## Use from the CLI

```bash
uv run travelops trip IST Kotor 2026-11-14 2026-11-16 --country Montenegro
uv run travelops airports Kotor --country Montenegro
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

The MCP tools are `search_trip`, `search_flights`, `search_stays`, `refine_flights`, `refine_stays`,
`stay_details`, `stay_photos`, `airports_near`, `sources`, and the price watch tools `watch_price`, `watches` and
`stop_watch`. `search_trip` answers a request such as "Montenegro, 22 to 23 October, staying in Kotor" in one
call: it finds the place, picks the airports that serve it, and searches flights there and back and stays for the
same dates.

Every search is remembered whole, and each result carries a `search_id`. A follow-up ("evening flights only",
"with a bag", "no dormitories, under 40 EUR") is answered by a refine tool from memory: no request to any site,
no waiting. Each filter reports how many offers it hid, and separately how many it hid only because the source
does not say. The same search within 30 minutes is answered from memory too; `refresh=true` forces new prices.

`scripts/call_tool.py` calls any tool from a terminal and prints exactly what an agent receives. The search tools
return the cheapest cards (`limit`) with `shown` telling how many exist, a short `report` of who answered and where
the answer is narrower than the question, plus exact statuses, timestamps, links, original currencies and
conversion-rate dates.

## Watching prices

A watch is a saved search that runs again on its own and alerts when the cheapest offer that passes its filters
falls. An agent saves one with `watch_price`; the command line does the same:

```bash
uv run travelops watch add flights '{"origin": "BEG", "destination": "LIS", "depart": "2026-11-14", "return_date": "2026-11-16"}' --filters '{"max_stops": 0}' --drop 5 --every 6
uv run travelops watch list
uv run travelops watch run
```

`watch run` checks the watches that are due, one search at a time, and exits; schedule it with cron or a systemd
timer, or keep it running with `--every-minutes 30`. Checks are at least three hours apart, at most twenty
watches run at once, and a watch ends on its departure or check-in day. A watch alerts when the price is
`--drop` percent below the price it last told (the first check, then each alert), or first reaches `--below`.
Alerts are printed; set `TRAVELOPS_NOTIFY_URL` to also POST them as plain text (an [ntfy](https://ntfy.sh) topic
URL works as is), or `TRAVELOPS_NOTIFY_COMMAND` to pipe them into a command.

## Checking the agent

`evals/` holds conversations over searches that really happened, replayed offline. `uv run travelops eval` checks
an agent's answers mechanically: every price and link must come from a tool result, a follow-up must not spend a
search, every source must be reported. See `evals/README.md`.

## Sources

| Source | Search | Access method |
| --- | --- | --- |
| Aviasales | Flights | Browser session and HTTP requests; prices of every selling agency |
| Tutu | Flights | Read-only Tutu MCP search |
| OneTwoTrip | Flights | HTTP requests |
| Kupibilet | Flights | HTTP requests |
| Wildberries Travel | Flights | Browser session and HTTP requests |
| Kiwi.com | Flights | Kiwi's public MCP server; prices in EUR, sold outside Russia |
| Booking.com | Stays | Browser session and HTTP requests |
| Airbnb | Stays | HTTP requests |
| trivago | Stays | trivago's public MCP server; one advertiser's price per stay |

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
