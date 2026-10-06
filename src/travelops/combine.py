"""A round trip as two separate tickets. Each direction is searched one way; the cheapest way out and the
cheapest way back need not be sold together, nor use the same airport. Two tickets are two contracts: if the
first flight is cancelled or moved, nobody owes the traveller the second."""

from __future__ import annotations

from .serialize import _leg_key

RISK = (
    "Two separate tickets: a change or a cancellation of one does not protect the other, and each is paid and "
    "refunded on its own terms."
)


def _best(card: dict, currency: str) -> tuple[float, dict] | None:
    """The cheapest fare of a one-way card that can be added up in the currency of the result."""
    fares = [f for g in card["groups"] for f in g["fares"]]
    priced = [
        (float(f["converted"]["amount"]) if f["converted"] else float(f["price"]["amount"]), f)
        for f in fares
        if f["converted"] or f["price"]["currency"] == currency
    ]
    return min(priced, key=lambda item: item[0]) if priced else None


def _leg(card: dict, fare: dict) -> dict:
    segments = card["outbound"]
    return {
        "flights": [s["flight"] for s in segments],
        "origin": segments[0]["origin"],
        "destination": segments[-1]["destination"],
        "departs": segments[0]["departs"],
        "arrives": segments[-1]["arrives"],
        "stops": card["stops"],
        **{k: fare[k] for k in ("price", "converted", "seller", "link", "seen_at", "baggage")},
    }


def separate_tickets(out: dict, back: dict, round_trip: dict | None, limit: int = 5, pool: int = 12) -> dict:
    """The cheapest pairs of one way out and one way back, from two filtered one-way views (cards cheapest first).
    Every flight appears in at most two pairs, so the list is a choice and not one flight five times."""
    currency = out["currency"]
    ways = []
    for view in (out, back):
        found = []
        for card in view["cards"][:pool]:
            best = _best(card, currency)
            if best:
                found.append((best[0], card, best[1]))
        ways.append(found)
    pairs = sorted(
        (
            (a[0] + b[0], a, b)
            for a in ways[0]
            for b in ways[1]
            if b[1]["outbound"][0]["departs"] > a[1]["outbound"][-1]["arrives"]
        ),
        key=lambda item: item[0],
    )
    used: dict[tuple, int] = {}
    chosen = []
    for total, a, b in pairs:
        keys = (("out", _leg_key(a[1]["outbound"])), ("back", _leg_key(b[1]["outbound"])))
        if any(used.get(key, 0) >= 2 for key in keys):
            continue
        for key in keys:
            used[key] = used.get(key, 0) + 1
        outbound, inbound = _leg(a[1], a[2]), _leg(b[1], b[2])
        chosen.append(
            {
                "total": {"amount": f"{total:.2f}", "currency": currency},
                "open_jaw": outbound["destination"] != inbound["origin"]
                or outbound["origin"] != inbound["destination"],
                "outbound": outbound,
                "return": inbound,
            }
        )
        if len(chosen) == limit:
            break
    result = {
        "pairs": chosen,
        "pairs_possible": len(pairs),
        "risk": RISK,
        "outbound_search_id": out.get("search_id"),
        "return_search_id": back.get("search_id"),
        # Who answered each one-way search: a seller missing here is missing from the pairs.
        "outbound_report": out.get("report"),
        "return_report": back.get("report"),
        "note": "`total` adds the two prices in the currency of the result. `open_jaw` is true when the trip "
        "lands at one airport and leaves from another. For other times or airports, refine each one-way search "
        "by its id.",
    }
    cheapest = None
    if round_trip and round_trip["cards"]:
        best = _best({"groups": round_trip["cards"][0]["groups"]}, currency)
        cheapest = best[0] if best else None
    if cheapest is not None and chosen:
        result["cheapest_round_trip"] = {"amount": f"{cheapest:.2f}", "currency": currency}
        saving = cheapest - float(chosen[0]["total"]["amount"])
        result["separate_is_cheaper_by"] = {"amount": f"{saving:.2f}", "currency": currency} if saving > 0 else None
    return result
