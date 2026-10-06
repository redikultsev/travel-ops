"""Turn a search that was just made into a recording an agent can be checked against, offline.

Search first (the usual way, live), then within half an hour:

    uv run python scripts/eval_record.py evals/scenarios/kotor trip BEG Kotor 2026-10-22 2026-10-23 --country Montenegro
    uv run python scripts/eval_record.py evals/scenarios/x flights BEG MOW 2026-11-14
    uv run python scripts/eval_record.py evals/scenarios/x stays Belgrade 2026-11-14 2026-11-16

It makes no request to a travel site: it copies what memory holds, cut to the cheapest cards. Opaque booking links
are replaced by stand-ins of a similar shape, so that no session of the person who recorded is published.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from travelops.app import build
from travelops.kinds import KINDS, flight_query, stay_query
from travelops.searches import Searches
from travelops.rates import load_rates
from travelops.replay import read_json, write_json
from travelops.trip import plan_trip

ROOT = Path(__file__).resolve().parents[1]
OPAQUE = re.compile(r"(https://www\.onetwotrip\.com/ru/f/book/)(\S+)")


def stand_in(url: str) -> str:
    """Keep a link that is too long for a chat too long, without keeping what it carries."""
    match = OPAQUE.fullmatch(url)
    if not match:
        return url
    return match[1] + (hashlib.sha256(url.encode()).hexdigest() * 12)[:700]


def trim(result: dict, cards: int, offers: int) -> dict:
    result["cards"] = result["cards"][:cards]
    for card in result["cards"]:
        for group in card.get("groups", []):
            group["fares"] = group["fares"][:offers]
            for fare in group["fares"]:
                if fare["link"]:
                    fare["link"]["url"] = stand_in(fare["link"]["url"])
        if "rates" in card:
            card["rates"] = card["rates"][:offers]
    return result


async def main(args) -> int:
    app = build(ROOT)
    touched: list[str] = []
    ask = app.net.request

    async def recording(source, method, url, **kw):
        from travelops.net.cache import RawCache

        touched.append(RawCache.key(method, url, kw.get("params"), kw.get("json", kw.get("data"))))
        return await ask(source, method, url, **kw)

    app.net.request = recording
    try:
        currency = app.profile.currency
        searches = Searches(app)
        await load_rates(app.net)
        wanted = []
        if args.kind == "trip":
            plan = await plan_trip(
                app,
                args.a,
                args.b,
                args.c,
                args.d,
                country=args.country,
                adults=args.adults,
                separate=args.separate,
                children_ages=args.children_ages,
            )
            wanted = [("flights", query, KINDS["flights"].sources()) for query in plan.flight_queries()]
            wanted.append(("stays", plan.stays, KINDS["stays"].sources()))
        elif args.kind == "flights":
            query = flight_query(app.profile, args.a, args.b, args.c, args.d, 0, args.adults)
            wanted = [("flights", query, KINDS["flights"].sources())]
        else:
            wanted = [("stays", stay_query(app.profile, args.a, args.b, args.c, args.adults), KINDS["stays"].sources())]
        out = Path(args.directory)
        (out / "memory").mkdir(parents=True, exist_ok=True)
        for kind, query, sources in wanted:
            stored = searches.remembered(kind, query, sources, currency)
            if stored is None:
                print(f"no {kind} search for this in memory from the last 30 minutes: search first")
                return 1
            row = {
                "id": stored.id,
                "kind": kind,
                "key": searches.key(kind, query, sources, currency),
                "at": stored.at,
                "result": trim(stored.result, args.cards, args.offers),
            }
            write_json(out / "memory" / f"{stored.id}.json.gz", row)
            print(f"{kind}: {stored.id}, {len(row['result']['cards'])} cards")
            if kind == "stays":
                # Property pages that were read for these stays go along, so that a replay can show them.
                pages = []
                for card in row["result"]["cards"]:
                    stay = card["stay"]
                    known = app.results.details(stay["source"], stay["source_id"])
                    if known:
                        known.pop("seen_at", None)
                        pages.append({"source": stay["source"], "source_id": stay["source_id"], "details": known})
                if pages:
                    write_json(out / "details.json", pages)
                    print(f"details: {len(pages)} property pages")
        rows = []
        for key in dict.fromkeys(touched):
            source, url, status, body = app.net.cache.db.execute(
                "SELECT source, url, status, body FROM raw WHERE key = ?", (key,)
            ).fetchone()
            rows.append({"key": key, "source": source, "url": url, "status": status, "body": body.decode()})
        write_json(out / "raw.json", rows)
        if not (out / "scenario.yml").exists():
            newest = max(read_json(path)["at"] for path in (out / "memory").glob("*.json*"))
            moment = datetime.fromtimestamp(newest + 30, timezone.utc).isoformat(timespec="seconds")
            (out / "scenario.yml").write_text(f'now: "{moment}"\ncontext: ""\nturns:\n  - say: ""\n')
            print("wrote a scenario.yml to fill in")
        print(f"raw answers: {[r['source'] for r in rows]}")
        return 0
    finally:
        await app.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory")
    parser.add_argument("kind", choices=("trip", "flights", "stays"))
    parser.add_argument("a", help="origin, or the place of a stay")
    parser.add_argument("b", help="place or destination, or the check-in date of a stay")
    parser.add_argument("c", help="departure date, or the check-out date of a stay")
    parser.add_argument("d", nargs="?", help="return date")
    parser.add_argument("--country")
    parser.add_argument("--adults", type=int)
    parser.add_argument("--separate", action="store_true", help="the trip was searched with separate_tickets")
    parser.add_argument("--children-ages", type=lambda v: [int(x) for x in v.split(",")], help="e.g. 7 or 3,9")
    parser.add_argument("--cards", type=int, default=120)
    parser.add_argument("--offers", type=int, default=5)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
