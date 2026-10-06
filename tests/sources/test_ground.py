import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from travelops.core.ground import GroundQuery
from travelops.net.client import Response
from travelops.sources.base import Context, NotConfigured
from travelops.sources.ground import tutu
from travelops.sources.ground.tutu import Source as Tutu
from travelops.sources.ground.twelvego import Source as TwelveGo

FIXTURES = Path(__file__).parents[1] / "fixtures"
NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)


def raw(path: str) -> bytes:
    return (FIXTURES / path).read_bytes()


def test_tutu_train_prices_the_party_from_one_seat():
    query = GroundQuery("Moscow", "Saint Petersburg", date(2026, 11, 14), adults=2, modes=("train",))
    parsed = Tutu().parse([raw("tutu-ground/rail-moscow-spb-2026-11-14.json")], query, NOW)
    cheapest = min(parsed.offers, key=lambda o: o.price.amount)
    assert str(cheapest.price.amount) == "2159.98" and cheapest.price.currency == "RUB"
    assert cheapest.price_from and str(cheapest.classes["seat"].amount) == "1079.99"
    ride = cheapest.rides[0]
    assert ride.mode == "train" and ride.number == "746У" and ride.carrier == "ФПК"
    assert ride.origin.startswith("Москва — Ленинградский") and cheapest.duration_min() == 320
    assert cheapest.link.kind == "results" and cheapest.rating == 9.4
    assert "train: results truncated: 5 of 40" in parsed.notes
    assert "train: searched Москва (Москва) to Санкт-Петербург (Санкт-Петербург)" in parsed.notes


def test_tutu_bus_price_is_already_the_party():
    query = GroundQuery("Belgrade", "Vienna", date(2026, 11, 14), adults=2, modes=("bus",))
    parsed = Tutu().parse([raw("tutu-ground/bus-belgrade-vienna-2026-11-14.json")], query, NOW)
    (offer,) = parsed.offers
    assert str(offer.price.amount) == "8484.0" and not offer.price_from and offer.classes == {}
    assert offer.rides[0].carrier == "Litas" and offer.duration_min() == 530
    # A name Tutu had several places for is said as a limit, so the agent checks the place.
    assert any(n.startswith("bus: understood as Белград") and "3 others" in n for n in parsed.notes)


def test_tutu_refuses_an_unknown_basis():
    payload = json.loads(raw("tutu-ground/bus-belgrade-vienna-2026-11-14.json"))
    payload["meta"]["pricing"]["basis"] = "per_seat"
    query = GroundQuery("Belgrade", "Vienna", date(2026, 11, 14), modes=("bus",))
    with pytest.raises(Exception, match="price basis"):
        Tutu().parse([json.dumps(payload).encode()], query, NOW)


async def test_tutu_calls_only_search_tools_with_russian_names(monkeypatch):
    calls = []

    class Net:
        async def request(self, source, method, url, **kw):
            calls.append(kw["json"])
            if kw["json"].get("method") == "tools/call":
                body = {"offers": [], "meta": {"pricing": {"basis": "per_seat"}, "checkout_hint": "x"}}
                answer = {"jsonrpc": "2.0", "id": 2, "result": {"structuredContent": body}}
            else:
                answer = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}
            return Response(200, json.dumps(answer).encode(), {"mcp-session-id": "s"})

    async def russian(ctx, place):
        return {"Belgrade": "Белград, Сербия", "Vienna": "Вена, Австрия"}[place]

    monkeypatch.setattr(tutu, "russian", russian)
    query = GroundQuery("Belgrade", "Vienna", date(2026, 11, 14), adults=1, children=1)
    raws = await Tutu().fetch(query, Context(Net(), None, lambda: NOW))
    tools = [c["params"]["name"] for c in calls if c.get("method") == "tools/call"]
    assert tools == ["search_bus"]  # a child's train fare is not known: the train is not asked
    bus = next(c for c in calls if c.get("method") == "tools/call")["params"]["arguments"]
    assert bus["origin"] == "Белград, Сербия" and bus["children"] == 1
    assert json.loads(raws[0]) == {"mode": "train", "skipped": "children's train fares are not priced"}
    assert "checkout_hint" not in json.loads(raws[1])["meta"]


def test_12go_drops_stops_short_of_the_place_and_prices_each_seat():
    query = GroundQuery("Belgrade", "Sarajevo", date(2026, 11, 14), adults=2, modes=("train", "bus"))
    parsed = TwelveGo().parse([raw("12go/belgrade-sarajevo-2026-11-14.json")], query, NOW)
    assert [str(o.price.amount) for o in parsed.offers] == ["60", "70"]
    assert all(o.rides[-1].destination == "Sarajevo East Bus Station" for o in parsed.offers)
    assert parsed.offers[0].duration_min() is None  # local times only
    assert "rides to stops short of Sarajevo left out: Ustipraca" in parsed.notes


def test_12go_keeps_modes_not_asked_out():
    query = GroundQuery("Belgrade", "Sarajevo", date(2026, 11, 14), modes=("train",))
    parsed = TwelveGo().parse([raw("12go/belgrade-sarajevo-2026-11-14.json")], query, NOW)
    assert parsed.offers == [] and "also found by bus, not asked for" in parsed.notes


async def test_12go_does_not_guess_children():
    with pytest.raises(NotConfigured):
        await TwelveGo().fetch(GroundQuery("A", "B", date(2026, 11, 14), children=1), Context(None, None, lambda: NOW))
