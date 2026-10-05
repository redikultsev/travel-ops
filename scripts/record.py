"""Record one source search, keeping persistent limits and scrubbing fixture bodies."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from travelops.core.flights import FlightQuery
from travelops.core.stays import StayQuery
from travelops.net.browser import BrowserSessions
from travelops.net.client import Blocked, Net
from travelops.net.limiter import Limiter, Quarantined
from travelops.scrub import scrub
from travelops.sources.base import Context
import travelops.sources as registry

ROOT = Path(__file__).resolve().parents[1]
FLIGHTS = ("aviasales", "tutu", "onetwotrip", "kupibilet", "wildberries")
STAYS = ("booking", "airbnb")


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=FLIGHTS + STAYS)
    domains = parser.add_subparsers(dest="kind", required=True)
    flights = domains.add_parser("flights")
    flights.add_argument("origin")
    flights.add_argument("destination")
    flights.add_argument("depart", type=date.fromisoformat)
    flights.add_argument("--return", dest="return_", type=date.fromisoformat)
    flights.add_argument("--adults", type=int, default=1)
    flights.add_argument("--cabin", default="economy")
    stays = domains.add_parser("stays")
    stays.add_argument("place")
    stays.add_argument("checkin", type=date.fromisoformat)
    stays.add_argument("checkout", type=date.fromisoformat)
    stays.add_argument("--adults", type=int, default=2)
    args = parser.parse_args()
    if (args.source in FLIGHTS) != (args.kind == "flights"):
        parser.error("source does not support this query kind")
    return args


async def record(args) -> int:
    if args.kind == "flights":
        query = FlightQuery(
            (args.origin.upper(),),
            (args.destination.upper(),),
            args.depart,
            return_=args.return_,
            adults=args.adults,
            cabin=args.cabin,
        )
        slug = f"{args.origin}-{args.destination}-{args.depart}"
    else:
        query = StayQuery(args.place, args.checkin, args.checkout, adults=args.adults)
        slug = f"{args.place}-{args.checkin}-{args.checkout}"
    # A fetch implementation exists before its parser is ready for registry use.
    module = importlib.import_module(f"travelops.sources.{args.kind}.{args.source}")
    source = module.Source()
    data_dir = Path(os.environ.get("TRAVELOPS_DATA", ROOT / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    limiter = Limiter(data_dir / "limiter.sqlite", getattr(registry, "RULES", {}))
    net = Net(data_dir, limiter=limiter)
    browser = BrowserSessions(data_dir, exit_=net.exit, proxy=net.proxy, limiter=limiter, counts=net.counts)
    ctx = Context(net, browser, lambda: datetime.now(timezone.utc))
    try:
        raws = await asyncio.wait_for(source.fetch(query, ctx), timeout=180)
        directory = ROOT / "tests" / "fixtures" / args.source
        directory.mkdir(parents=True, exist_ok=True)
        slug = "-".join(slug.lower().split()).replace("/", "-")
        for index, raw in enumerate(raws):
            body = scrub(raw)
            try:
                json.loads(body)
                extension = "json"
            except ValueError:
                extension = "html"
            path = directory / f"{slug}-{index}.{extension}"
            path.write_bytes(body)
            print(f"{path.relative_to(ROOT)}: {len(body)} bytes")
        print(f"Requests: {net.counts[source.name]}")
        return 0
    except (Blocked, Quarantined, TimeoutError) as exc:
        print(f"{source.name}: {type(exc).__name__}: {exc}")
        return 2
    finally:
        await net.close()
        net.cache.db.close()
        limiter.db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(record(arguments())))
