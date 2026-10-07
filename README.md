# travel-ops

Search live flight, stay, train and bus offers through a command line interface or an MCP-compatible agent. The engine
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

## Run in a container

The `Dockerfile` builds both browsers into one image (about 5 GB, most of it Camoufox) and runs the MCP server
over HTTP as a user without root. The profile is mounted, the data lives on a volume:

```bash
docker build -t travel-ops .
docker run -d --name travel-ops -v travel-data:/data -v "$PWD/profile.yml:/app/profile.yml:ro" --shm-size 1g travel-ops
docker run -d --name travel-watch -v travel-data:/data -v "$PWD/profile.yml:/app/profile.yml:ro" --shm-size 1g \
  travel-ops travelops watch run --every-minutes 30
```

Both browsers are pinned in the `Dockerfile`: Google Chrome by version and the SHA256 its repository index lists
(`CHROME_VERSION`, `CHROME_SHA256`), Camoufox by release (`CAMOUFOX_VERSION`). Rebuild monthly: take the current
stable from `https://dl.google.com/linux/chrome/deb/dists/stable/main/binary-amd64/Packages` (its `Version` and
`SHA256`) and the current Camoufox release (`python -m camoufox list`), move the pins, and build with
`--no-cache`. Google keeps only recent Chrome packages, so an old pin stops building: that is the reminder.
Set `TRAVELOPS_CHROME_SANDBOX=1` where the container allows Chrome's sandbox (`travelops doctor` says if it starts).

`travelops mcp --http` answers MCP at `/mcp` on port 8765: one POST per tool call, JSON in and out, no session.
It has no authentication: publish the port nowhere, and put the container on a network where the only other
member is the client that may call it.

## Use from the CLI

```bash
uv run travelops trip IST Kotor 2026-11-14 2026-11-16 --country Montenegro
uv run travelops airports Kotor --country Montenegro
uv run travelops flights IST MOW 2026-11-14
uv run travelops flights MOW LED 2026-11-14 --flex 1 --sources aviasales,tutu
uv run travelops stays Lisbon 2026-11-14 2026-11-16
uv run travelops ground Belgrade Vienna 2026-11-14 --adults 2
uv run travelops sources
uv run travelops doctor
```

Use `travelops --help` for all options. The screen shows the 15 cheapest cards and says how many exist; `--limit N`
changes that and `--json` prints everything. A command line search always asks the sites for new prices, starts from
the profile's bars as an agent's search does (it says what they hid), and is kept in memory: its `search_id` can be
refined by an agent afterwards. Long searches may ask for confirmation before they start. Source limits,
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

The MCP tools are `search_trip`, `search_flights`, `search_stays`, `search_ground`, `refine_flights`,
`refine_stays`, `refine_ground`, `stay_details`, `stay_photos`, `airports_near`, `sources`, and the price watch
tools `watch_price`, `watches`, `watch_alerts` and `stop_watch`. `search_trip` answers a request such as "Montenegro, 22 to 23 October, staying in Kotor" in one
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
Alerts are kept until collected: an assistant calls `watch_alerts` (or `travelops watch alerts`) and tells the
human; each alert is given out once. A collector that keeps alerts before telling (a router that must not lose
one across a restart) looks with `take=false`, keeps them, and then marks exactly those with `upto`, the last
`alert_id` it kept. To push them as well, set `TRAVELOPS_NOTIFY_URL` to POST each as plain text
(an [ntfy](https://ntfy.sh) topic URL works as is), or `TRAVELOPS_NOTIFY_COMMAND` to pipe each, as JSON, into a
command. Watches cover flights, stays, and trains and buses.

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
| Google Flights | Flights | Google's public search page, no API (see its terms); worldwide |
| Trip.com | Flights | A browser opens its results page; one way only; Asia and worldwide |
| Booking.com | Stays | Browser session and HTTP requests |
| Airbnb | Stays | HTTP requests |
| trivago | Stays | trivago's public MCP server; one advertiser's price per stay |
| Tutu | Trains and buses | Tutu MCP search; Russia and the CIS, some international buses |
| 12Go | Trains, buses, ferries | 12Go's public MCP server; Turkey, south-east Asia, parts of the Balkans |
| FlixBus | Buses and trains | FlixBus shop JSON; Europe and North America, partner carriers included |

Sites may limit automated requests or block them. Respect each site's terms and use the tool for personal-volume
searches. Do not bypass blocks with proxies or use booking, checkout, or passenger-data flows.

## Development

Offline tests are the default and do not send requests to travel sites:

```bash
uv run pytest -q
```

The words the code is written in (kind, search, view, card, offer, watch) are in [CONTEXT.md](CONTEXT.md).

Live source checks consume persistent per-source request budgets and should be run only when explicitly intended.
Never run them in a loop or concurrently. See [NOTICE](NOTICE) for project attributions and currency-rate
attribution.
