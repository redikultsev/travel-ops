"""Command line searches and installation diagnostics."""

import argparse
import asyncio
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import shutil
import sys
import time
from rich.console import Console
from .app import build
from .core.report import Status
from .kinds import KINDS
from .profile import data_dir
from .rates import load_rates
from .search import run_source
from .searches import Searches


def parser():
    p = argparse.ArgumentParser(
        prog="travelops", description="Live flight and stay search; read-only, with source reports."
    )
    commands = p.add_subparsers(dest="command", required=True)
    f = commands.add_parser("flights")
    f.add_argument("origin")
    f.add_argument("destination")
    f.add_argument("depart", type=date.fromisoformat)
    f.add_argument("--return", dest="return_date", type=date.fromisoformat)
    f.add_argument("--flex", dest="flex_days", type=int, default=0)
    f.add_argument("--cabin", choices=("economy", "premium_economy", "business", "first"))
    s = commands.add_parser("stays")
    s.add_argument("place")
    s.add_argument("checkin", type=date.fromisoformat)
    s.add_argument("checkout", type=date.fromisoformat)
    g = commands.add_parser("ground", help="trains and buses, one way, between two places named in Latin script")
    g.add_argument("origin")
    g.add_argument("destination")
    g.add_argument("depart", type=date.fromisoformat)
    g.add_argument("--modes", type=lambda value: value.split(","), help="comma-separated: train, bus, ferry, van")
    for sub in (f, s, g):
        sub.add_argument("--adults", type=int)
        sub.add_argument("--sources", type=lambda value: value.split(","))
        sub.add_argument("--currency")
        sub.add_argument("--proxy")
        sub.add_argument("--json", action="store_true")
        sub.add_argument("--yes", action="store_true")
        sub.add_argument("--limit", type=int, help="cards to show; default 15 on screen, everything with --json")
    t = commands.add_parser("trip", help="flights to the airports near a place and back, and stays there")
    t.add_argument("origin")
    t.add_argument("place")
    t.add_argument("depart", type=date.fromisoformat)
    t.add_argument("return_date", type=date.fromisoformat, nargs="?", help="omit for a one-way trip")
    t.add_argument("--checkout", type=date.fromisoformat, help="last day of the stay, when not the flight back")
    t.add_argument("--flex", dest="flex_days", type=int, default=0, help="also try the trip shifted by 1 to 3 days")
    t.add_argument("--separate", action="store_true", help="also price each direction as its own ticket")
    t.add_argument("--country")
    t.add_argument("--airports", help="destination airports to search instead of the nearest ones")
    t.add_argument("--max-airports", type=int, default=2)
    t.add_argument("--adults", type=int)
    t.add_argument("--max-stops", type=int)
    t.add_argument("--currency")
    t.add_argument("--proxy")
    t.add_argument("--limit", type=int, default=5)
    t.add_argument("--yes", action="store_true")
    a = commands.add_parser("airports", help="airports near a place, nearest first")
    a.add_argument("place")
    a.add_argument("--country")
    a.add_argument("--radius", type=int, default=100)
    src = commands.add_parser("sources")
    src.add_argument("--reset", metavar="BUCKET")
    d = commands.add_parser("doctor")
    d.add_argument("--live", action="store_true")
    commands.add_parser("mcp")
    w = commands.add_parser("watch", help="price watches: saved searches repeated by `watch run`")
    actions = w.add_subparsers(dest="action", required=True)
    wa = actions.add_parser("add", help="save a watch; ARGUMENTS as the search tool takes them, in JSON")
    wa.add_argument("kind", choices=tuple(KINDS))
    wa.add_argument("arguments", type=json.loads)
    wa.add_argument("--filters", type=json.loads, default=None, help="refine tool arguments, in JSON")
    wa.add_argument("--below", type=float)
    wa.add_argument("--drop", type=float, default=5.0, help="alert when this many percent cheaper")
    wa.add_argument("--every", type=float, default=6.0, help="hours between checks, at least 3")
    wa.add_argument("--currency")
    actions.add_parser("list").add_argument("--all", action="store_true", help="stopped watches too")
    actions.add_parser("stop").add_argument("watch_id")
    actions.add_parser(
        "alerts", help="price drops not yet collected; --peek leaves them for the assistant"
    ).add_argument("--peek", action="store_true")
    wr = actions.add_parser("run", help="check the watches that are due, one search at a time")
    wr.add_argument("--every-minutes", type=float, help="keep running, looking for due watches this often")
    e = commands.add_parser("eval", help="check an agent's answers to a recorded scenario; no network")
    e.add_argument("scenario", help="a directory under evals/scenarios")
    e.add_argument("run", help="a directory with turn-N.md and turn-N.trace.jsonl")
    return p


def amount(value):
    return f"{value['amount']} {value['currency']}" if value else "conversion unavailable"


def reports(console, source_reports):
    for report in source_reports:
        console.print(
            f"{report['source']}: {report['status']} — {report['reason'] or str(report['offers']) + ' offers'}; {report['requests']} requests",
            markup=False,
        )
        for note in report["notes"]:
            console.print(f"  {note}", markup=False)


def show(console, result, kind):
    if not result["cards"]:
        console.print("No priced offers returned.")
    for index, card in enumerate(result["cards"], 1):
        if kind == "flights":
            console.print(
                f"\n{index}. {' → '.join(card['route'])} | {card['duration_min']} min door to door", markup=False
            )
            if card["return_duration_min"] is not None:
                console.print(f"Return: {card['return_duration_min']} min door to door")
            for left, right in card["airport_changes"]:
                console.print(f"Airport change: {left} → {right}", markup=False)
            for label in ("outbound", "inbound"):
                for segment in card[label]:
                    console.print(
                        f"{label}: {segment['flight']} {segment['origin']} {segment['departs']} → {segment['destination']} {segment['arrives']}",
                        markup=False,
                    )
            for group in card["groups"]:
                for rank, fare in enumerate(group["fares"][:3]):
                    price = (
                        amount(fare["converted"]) + " (" + amount(fare["price"]) + ")"
                        if fare["converted"]
                        else amount(fare["price"])
                    )
                    console.print(
                        f"{'Best' if rank == 0 else 'Competitor'}: {price} | {fare['seller']} | {fare['cabin'] or 'cabin unknown'}",
                        markup=False,
                    )
                    bag = fare["baggage"]
                    console.print(
                        f"Checked pieces: {bag['checked'] if bag['checked'] is not None else 'unknown'}; kg: {bag['checked_kg'] if bag['checked_kg'] is not None else 'unknown'}; carry-on: {bag['carry_on'] if bag['carry_on'] is not None else 'unknown'}",
                        markup=False,
                    )
                    console.print(
                        f"Seen: {fare['seen_at']}; refundable: {fare['refundable'] if fare['refundable'] is not None else 'unknown'}",
                        markup=False,
                    )
                    link = fare["link"]
                    console.print(f"{link['kind']}: {link['url']}" if link else "Link unavailable", markup=False)
                if len(group["fares"]) > 3:
                    console.print(f"Shown 3 of {len(group['fares'])} comparable seller fares; --json includes all.")
        elif kind == "ground":
            fare, rides = card["fares"][0], card["rides"]
            stops = " → ".join(f"{r['from']} {r['departs'][11:16]}" for r in rides)
            price = amount(fare["converted"] or fare["price"]) + (" from" if fare["price_from"] else "")
            console.print(
                f"\n{index}. {stops} → {rides[-1]['to']} {rides[-1]['arrives'][11:16]} | {'/'.join(card['modes'])}"
                f" | {price} | {fare['source']}",
                markup=False,
            )
            link = fare["link"]
            console.print(
                f"Seen: {fare['seen_at']}; " + (f"{link['kind']}: {link['url']}" if link else "Link unavailable"),
                markup=False,
            )
        else:
            stay = card["stay"]
            console.print(
                f"\n{index}. {stay['name']} | rating: {stay['rating'] if stay['rating'] is not None else 'unknown'}/10 | reviews: {stay['reviews'] if stay['reviews'] is not None else 'unknown'}",
                markup=False,
            )
            for rate in card["rates"]:
                console.print(
                    f"Total: {amount(rate['total'])}; per night: {amount(rate['per_night'])}; {card['nights']} nights",
                    markup=False,
                )
                if rate["converted"] and rate["converted"]["currency"] != rate["total"]["currency"]:
                    console.print(f"Converted total: {amount(rate['converted'])}", markup=False)
                console.print(
                    f"Cancellation cutoff: {rate['free_cancel_until'] or 'unknown'}; meals: {rate['meals'] or 'unknown'}; room: {rate['room'] or 'unknown'}",
                    markup=False,
                )
                console.print(f"Seen: {rate['seen_at']}; {rate['link']['kind']}: {rate['link']['url']}", markup=False)
            console.print("Photo: " + (stay["photos"][0] if stay["photos"] else "unknown"), markup=False)
    for entry in (result.get("filtered") or {}).get("by", []):
        if entry["hidden"] or entry.get("hidden_unknown"):
            console.print(
                f"Hidden by {entry['filter']} {entry['value']}: {entry['hidden']}"
                + (f", {entry['hidden_unknown']} more that do not say" if entry.get("hidden_unknown") else ""),
                markup=False,
            )
    shown = result.get("shown")
    if shown and shown["cards"] < shown["of"]:
        console.print(
            f"\nShown the {shown['cards']} cheapest of {shown['of']} cards; --limit N or --json for more.", markup=False
        )
    console.print(f"\nConversion rates: {result['rates_day']}", markup=False)
    reports(console, result["sources"])


async def doctor(app, live, console):
    from playwright.async_api import async_playwright

    checks = []

    def check(name, ok, detail, fix):
        checks.append(ok)
        console.print(f"{name}: {'ok' if ok else 'failed'} — {detail}" + ("" if ok else f"; {fix}"), markup=False)

    check("Python", sys.version_info >= (3, 12), sys.version.split()[0], "install Python 3.12 or newer")
    check("uv", bool(shutil.which("uv")), shutil.which("uv") or "not found", "install uv: https://docs.astral.sh/uv/")
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="chrome", headless=True)
            version = browser.version
            await browser.close()
            check("Google Chrome", True, version, "")
        except Exception:
            check(
                "Google Chrome",
                False,
                "not found",
                "install Google Chrome (on Linux: uv run playwright install --with-deps chrome, as root); "
                "Booking needs it to pass the anti-bot check",
            )
    try:
        from camoufox.async_api import AsyncCamoufox

        # Started, not just found: on a bare Linux server the files are there but its system libraries are not.
        async with AsyncCamoufox(headless=True) as browser:
            check("Camoufox", True, browser.version, "")
    except Exception as exc:
        check(
            "Camoufox",
            False,
            (str(exc).splitlines() or ["did not start"])[0],
            "run uv run python -m camoufox fetch; on Linux also uv run playwright install-deps firefox, as root",
        )
    path = data_dir(app.root)
    probe = path / ".write-check"
    try:
        probe.write_text("check")
        probe.unlink()
        check("Data directory", True, str(path), "")
    except OSError as exc:
        check("Data directory", False, str(exc), "set TRAVELOPS_DATA to a writable directory")
    try:
        rates = await load_rates(app.net)
        check("Rates feed", True, rates.day, "")
    except Exception as exc:
        check(
            "Rates feed",
            False,
            f"{type(exc).__name__}: {exc}",
            "check connectivity to open.er-api.com; no conversion is invented",
        )
    if live:
        # One search per source, through the same limiter as any search: a quarantined source reports that
        # and is not asked again until its rest is over.
        depart = date.today() + timedelta(days=40)
        from .search import deadline

        jobs = [(s, kind.sample(depart)) for kind in KINDS.values() for s in kind.sources()]
        results = await asyncio.gather(*(run_source(s, q, app.ctx, deadline(s, q, app.ctx, 120)) for s, q in jobs))
        source_reports = [report for _, report in results]
        from dataclasses import asdict

        reports(console, [asdict(report) for report in source_reports])
        checks.extend(r.status in (Status.OK, Status.EMPTY) for r in source_reports)
    return 0 if all(checks) else 1


async def watch(app, args):
    from mcp.server.mcpserver.exceptions import ToolError

    from .mcp import Tools
    from .watch import run_due

    tools = Tools(app)
    try:
        if args.action == "add":
            out = await tools.watch_price(
                args.kind, args.arguments, args.filters, args.below, args.drop, args.every, args.currency
            )
        elif args.action == "list":
            out = await tools.watches(include_stopped=args.all)
        elif args.action == "stop":
            out = await tools.stop_watch(args.watch_id)
        elif args.action == "alerts":
            out = await tools.watch_alerts(take=not args.peek)
        else:
            while True:
                stamp = datetime.now().isoformat(timespec="minutes")
                await run_due(tools, app.watches, lambda line: print(f"[{stamp}] {line}", flush=True))
                if not args.every_minutes:
                    return 0
                await asyncio.sleep(max(5.0, args.every_minutes) * 60)
    except ToolError as exc:
        raise ValueError(str(exc)) from exc
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


async def search(app, args, console):
    """flights, stays, ground: one search with new prices, kept in memory for an agent's follow-up questions."""
    searches = Searches(app)
    kind = args.command
    query = searches.kind(kind).read(app.profile, vars(args))
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be at least 1")
    estimate = searches.estimate(kind, [query], args.sources, args.currency, refresh=True)
    Console(stderr=True).print(f"Estimated request spacing: {estimate:.0f}s; server response time is additional.")
    if estimate > app.profile.confirm_over_seconds and sys.stdin.isatty() and not args.yes:
        if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
            return 0
    (stored,) = await searches.run(
        kind, [query], sources=args.sources, currency=args.currency, refresh=True, confirm=True
    )
    # On screen the 15 cheapest; with --json everything, unless --limit says otherwise.
    result = searches.view(kind, stored, args.limit or (None if args.json else 15))
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        show(console, result, kind)
    return 0


async def execute(args):
    app = build(Path.cwd(), getattr(args, "proxy", None))
    console = Console()
    try:
        if args.command == "sources":
            if args.reset:
                app.limiter.reset(args.reset)
                console.print(f"Reset {args.reset}", markup=False)
            for kind in KINDS.values():
                console.print(f"Known {kind.name}: " + ", ".join(s.name for s in kind.sources()))
            for bucket, interval, until in app.limiter.overview():
                console.print(
                    f"{bucket}: interval {interval:.1f}s; quarantine {max(0, until - time.time()):.0f}s remaining",
                    markup=False,
                )
            return 0
        if args.command == "doctor":
            return await doctor(app, args.live, console)
        if args.command == "watch":
            return await watch(app, args)
        if args.command == "airports":
            from .geo import airports_near, locate, place_json

            places = await locate(app.net, args.place, args.country)
            if not places:
                raise ValueError("place not found; write it in Latin script and add --country")
            here = places[0]
            print(
                json.dumps(
                    {"place": place_json(here), "airports": airports_near(here.lat, here.lon, args.radius)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "trip":
            from .trip import plan_trip, search_trip

            plan = await plan_trip(
                app,
                args.origin,
                args.place,
                args.depart,
                args.return_date,
                checkout=args.checkout,
                flex_days=args.flex_days,
                separate=args.separate,
                country=args.country,
                airports=args.airports,
                max_airports=args.max_airports,
                adults=args.adults,
            )
            searches = Searches(app)
            currency = searches.currency(args.currency)
            # A command typed by a human is a request for new prices.
            estimate = plan.estimate(searches, currency, refresh=True)
            Console(stderr=True).print(
                f"{plan.place.label()}: airports {', '.join(plan.flights.destinations)}; "
                f"estimated request spacing {estimate:.0f}s; server response time is additional.",
                markup=False,
            )
            if estimate > app.profile.confirm_over_seconds and sys.stdin.isatty() and not args.yes:
                if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
                    return 0
            result = await search_trip(
                searches, plan, currency, limit=args.limit, max_stops=args.max_stops, refresh=True
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0
        return await search(app, args, console)
    finally:
        await app.close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "mcp":
            from .mcp import create_server

            create_server(Path.cwd()).run(transport="stdio")
            return 0
        if args.command == "eval":
            from .evals import check_run

            report = check_run(Path(args.scenario), Path(args.run))
            for turn in report:
                verdict = "pass" if not turn["failures"] else "FAIL"
                print(f"turn {turn['turn']}: {verdict} — calls: {', '.join(turn['calls']) or 'none'}")
                for failure in turn["failures"]:
                    print(f"  - {failure}")
            return 1 if any(turn["failures"] for turn in report) else 0
        return asyncio.run(execute(args))
    except (ValueError, OSError) as exc:
        Console(stderr=True).print(f"Error: {exc}", markup=False)
        return 2
    except KeyboardInterrupt:
        return 130
