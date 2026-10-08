"""The same property's price on the other sources. A search lists each source's own first page, ranked its own way,
so a hotel found on one is seldom on another's page; it is asked for by name instead, one request or two per
source, for the few stays worth it. What is found joins the card of the search, so its views show it too."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from .cards import order
from .core.stays import Stay, StayQuery
from .memory import Stored
from .merge.stays import looked_up, place_words
from .net.browser import BrowserUnavailable
from .net.client import Blocked, Offline
from .net.limiter import Quarantined
from .serialize import rate_json
from .sources import STAY_SOURCES
from .sources.base import NotConfigured, ParseError, SourceFault
from .details import listings, pick

AT_MOST = 5
# Sources that can find one property by its name. Airbnb is not among them: a home there has a name no other site
# uses.
COMPARED = ("booking", "trivago", "tripcom", "googlehotels")


def _stay(listing: dict, card: dict) -> Stay:
    stay = card["stay"]
    return Stay(
        listing["source"],
        listing["source_id"],
        listing["name"],
        stay["kind"],
        stay.get("lat"),
        stay.get("lon"),
        listing.get("rating"),
        listing.get("reviews"),
        center_km=stay.get("center_km"),
    )


def _query(asked: dict) -> StayQuery:
    return StayQuery(
        asked["place"],
        date.fromisoformat(asked["checkin"]),
        date.fromisoformat(asked["checkout"]),
        asked["adults"],
        asked.get("children", 0),
        asked.get("rooms", 1),
    )


def lookup_text(name: str, place: str) -> str:
    city = place.split(",")[0].strip()
    return name if city.casefold() in name.casefold() else f"{name}, {city}"


async def compare_stays(app, stored: Stored, asked: list[str], rates, sources=COMPARED) -> list[dict]:
    if not asked or len(asked) > AT_MOST:
        raise ValueError(f"name from 1 to {AT_MOST} stays; each one is a request or two to every other source")
    result = stored.result
    query, currency = _query(result["query"]), result["currency"]
    ignore = place_words(query.place)
    out = []
    for name, card in pick(stored, asked):
        if card is None:
            out.append({"asked": name, "status": "not_found", "reason": "no single stay of this search has this name"})
            continue
        listed = listings(card)
        mine = _stay(listed[0], card)
        checked = {}
        for source_name in sources:
            if any(entry["source"] == source_name for entry in listed):
                checked[source_name] = "in the search already"
                continue
            if source_name not in STAY_SOURCES:
                checked[source_name] = "not_configured: no such source"
                continue
            source = STAY_SOURCES[source_name]()
            seen_at = datetime.now(timezone.utc)
            try:
                found = await asyncio.wait_for(
                    source.lookup(query, lookup_text(mine.name, query.place), app.ctx, seen_at), timeout=150
                )
            except (Blocked, Quarantined) as exc:
                checked[source_name] = f"blocked: {exc}"
                continue
            except (TimeoutError, asyncio.TimeoutError) as exc:
                checked[source_name] = f"timeout: {exc or 'no answer in 150 s'}"
                continue
            except ParseError as exc:
                checked[source_name] = f"unparsed: {exc}"
                continue
            except (NotConfigured, BrowserUnavailable) as exc:
                checked[source_name] = f"not_configured: {exc}"
                continue
            except (SourceFault, Offline) as exc:
                checked[source_name] = f"failed: {exc}"
                continue
            match = next(((o, how) for o in found if (how := looked_up(mine, o.stay, ignore))), None)
            if match is None:
                names = sorted({o.stay.name for o in found})[:3]
                checked[source_name] = "not_found" + (f": it answered with {', '.join(names)}" if names else "")
                continue
            offer, how = match
            same = [o for o in found if o.stay.source_id == offer.stay.source_id]
            listed.append(
                {
                    "source": source_name,
                    "source_id": offer.stay.source_id,
                    "name": offer.stay.name,
                    "rating": offer.stay.rating,
                    "reviews": offer.stay.reviews,
                    "matched": how,
                }
            )
            card["rates"].extend(rate_json(o.rate, query.nights, rates, currency) for o in same)
            if card["stay"].get("lat") is None and offer.stay.lat is not None:
                card["stay"]["lat"], card["stay"]["lon"] = offer.stay.lat, offer.stay.lon
            checked[source_name] = "ok"
        card["listed_on"] = listed
        card["rates"].sort(key=lambda rate: order(rate, currency))
        out.append(
            {
                "name": card["stay"]["name"],
                "listed_on": listed,
                "checked": checked,
                "rates": [
                    {
                        k: rate.get(k)
                        for k in (
                            "source",
                            "seller",
                            "total",
                            "all_in",
                            "all_in_converted",
                            "converted",
                            "per_night_converted",
                            "room",
                            "meals",
                            "free_cancellation",
                            "link",
                        )
                    }
                    for rate in card["rates"]
                ],
            }
        )
    app.results.rewrite(stored.id, result)
    return out
