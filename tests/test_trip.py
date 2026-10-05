from datetime import date

import pytest

from travelops import trip
from travelops.app import build
from travelops.core.money import Rates
from travelops.geo import Place
from travelops.search import FlightSearch, StaySearch
from travelops.serialize import drop_long_routes

KOTOR = Place("Kotor", "Montenegro", "ME", "Kotor", 42.421, 18.768)


@pytest.fixture
async def app(tmp_path):
    value = build(tmp_path, None)
    yield value
    await value.close()


@pytest.fixture
def located(monkeypatch):
    async def locate(net, name, country=None):
        return [] if name == "Nowhere" else [KOTOR, Place("Kotor", "Bosnia and Herzegovina", "BA", None, 44.6, 17.4)]

    monkeypatch.setattr(trip, "locate", locate)


async def test_plan_picks_the_nearest_airports_and_the_same_dates_for_the_stay(app, located):
    plan = await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", "2026-10-23")
    assert plan.flights.origins == ("BEG",) and plan.flights.destinations == ("TIV", "TGD")
    assert plan.flights.depart == date(2026, 10, 22) and plan.flights.return_ == date(2026, 10, 23)
    assert plan.stays.place == "Kotor, Montenegro" and plan.stays.nights == 1
    assert [a["iata"] for a in plan.airports][:3] == ["TIV", "TGD", "DBV"]
    assert plan.alternatives[0].country_code == "BA"


async def test_plan_respects_given_airports_and_party(app, located):
    plan = await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", "2026-10-23", airports="DBV", adults=2)
    assert plan.flights.destinations == ("DBV",) and plan.flights.adults == 2 and plan.stays.adults == 2
    solo = await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", "2026-10-23")
    assert solo.flights.adults == solo.stays.adults == 1, "who flies is who stays"
    apart = await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", "2026-10-23", adults=1, stay_adults=3)
    assert apart.stays.adults == 3


async def test_plan_refuses_what_it_cannot_search(app, located):
    with pytest.raises(ValueError, match="not found"):
        await trip.plan_trip(app, "BEG", "Nowhere", "2026-10-22", "2026-10-23")
    with pytest.raises(ValueError, match="both dates"):
        await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", None)
    with pytest.raises(ValueError, match="return"):
        await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", "2026-10-20")


async def test_search_runs_both_parts_and_reports_the_plan(app, located, monkeypatch):
    seen = {}

    async def flights(query, sources, ctx, rates, currency, **kwargs):
        seen["flights"] = query
        return FlightSearch(query, currency, [], [])

    async def stays(query, sources, ctx, rates, currency, **kwargs):
        seen["stays"] = query
        return StaySearch(query, currency, [], [])

    monkeypatch.setattr(trip, "search_flights", flights)
    monkeypatch.setattr(trip, "search_stays", stays)
    plan = await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", "2026-10-23")
    result = await trip.search_trip(app, plan, Rates("EUR", {}, "d"), "EUR", limit=3)
    assert seen["flights"].destinations == ("TIV", "TGD") and seen["stays"].checkout == date(2026, 10, 23)
    assert result["place"]["country_code"] == "ME"
    assert [(a["iata"], a["searched"]) for a in result["airports_in_reach"]][:3] == [
        ("TIV", True),
        ("TGD", True),
        ("DBV", False),
    ]
    assert result["flights"]["filtered"] == {"max_stops": 1, "hidden_cards": 0}
    assert result["flights"]["shown"]["cards"] == 0 and result["stays"]["shown"]["cards"] == 0
    assert result["stays"]["filtered"] == {"min_rating": 8.0, "hidden_below": 0, "hidden_unrated": 0}
    assert result["not_included"][0].startswith("transfer between the airport and the place")


def test_long_routes_are_hidden_with_a_count():
    result = drop_long_routes({"cards": [{"stops": 0}, {"stops": 2}, {"stops": 1}]}, 1)
    assert [c["stops"] for c in result["cards"]] == [0, 1] and result["filtered"] == {"max_stops": 1, "hidden_cards": 1}


def test_low_and_unrated_stays_are_hidden_with_counts():
    from travelops.serialize import drop_low_rated

    cards = [{"stay": {"rating": r}} for r in (9.1, 6.0, None, 8.0)]
    result = drop_low_rated({"cards": cards}, 8.0)
    assert [c["stay"]["rating"] for c in result["cards"]] == [9.1, 8.0]
    assert result["filtered"] == {"min_rating": 8.0, "hidden_below": 1, "hidden_unrated": 1}


def test_the_estimate_is_what_a_search_usually_costs_not_its_ceiling():
    from datetime import date as day
    from travelops.core.flights import FlightQuery
    from travelops.search import estimate_flights, expected_requests
    from travelops.sources.flights.aviasales import Source

    query = FlightQuery(("BEG",), ("TIV", "TGD"), day(2026, 10, 22), day(2026, 10, 23))
    source = Source()
    assert expected_requests(source, query) == 7 < source.max_requests(query)

    class Limiter:
        def estimate(self, bucket, n):
            return n * 10.0

    assert estimate_flights(query, [source], Limiter(), "") == 70.0
