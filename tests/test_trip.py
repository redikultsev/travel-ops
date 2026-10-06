from datetime import date

import pytest

from travelops import trip
from travelops.app import build
from travelops.core.money import Rates
from travelops.geo import Place
from travelops.search import FlightSearch, StaySearch
from travelops import recall

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

    async def roads(net, lat, lon, airports, at_most=4):
        return airports, "offline test: no road distances"

    monkeypatch.setattr(trip, "locate", locate)
    monkeypatch.setattr(trip, "with_roads", roads)


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
    with pytest.raises(ValueError, match="separate tickets need a return date"):
        await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", None, separate=True)
    with pytest.raises(ValueError, match="choose one"):
        await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", "2026-10-23", separate=True, flex_days=1)
    with pytest.raises(ValueError, match="at most 3"):
        await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", "2026-10-23", flex_days=7)
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

    monkeypatch.setattr(recall, "search_flights", flights)
    monkeypatch.setattr(recall, "search_stays", stays)
    plan = await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", "2026-10-23")
    result = await trip.search_trip(app, plan, Rates("EUR", {}, "d"), "EUR", limit=3)
    assert seen["flights"].destinations == ("TIV", "TGD") and seen["stays"].checkout == date(2026, 10, 23)
    assert result["place"]["country_code"] == "ME"
    assert [(a["iata"], a["searched"]) for a in result["airports_in_reach"]][:3] == [
        ("TIV", True),
        ("TGD", True),
        ("DBV", False),
    ]
    assert result["flights"]["filtered"] == {
        "hidden_cards": 0,
        "by": [
            {"filter": "max_stops", "value": 1, "hidden": 0},
            {"filter": "max_leg_hours", "value": 24, "hidden": 0},
        ],
    }
    assert result["flights"]["shown"]["cards"] == 0 and result["stays"]["shown"]["cards"] == 0
    assert result["stays"]["filtered"]["by"] == [{"filter": "min_rating", "value": 8.0, "hidden": 0}]
    assert result["not_included"][0].startswith("transfer between the airport and the place")
    assert result["flights"]["search_id"].startswith("f") and result["stays"]["search_id"].startswith("s")
    assert result["flights"]["from_memory"] is False and result["flights"]["age_minutes"] == 0


async def test_the_same_trip_again_is_answered_from_memory(app, located, monkeypatch):
    calls = []

    async def flights(query, sources, ctx, rates, currency, **kwargs):
        calls.append("flights")
        return FlightSearch(query, currency, [], [])

    async def stays(query, sources, ctx, rates, currency, **kwargs):
        calls.append("stays")
        return StaySearch(query, currency, [], [])

    monkeypatch.setattr(recall, "search_flights", flights)
    monkeypatch.setattr(recall, "search_stays", stays)
    plan = await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", "2026-10-23")
    rates = Rates("EUR", {}, "d")
    assert plan.estimate(app, "EUR") > 0
    first = await trip.search_trip(app, plan, rates, "EUR")
    assert plan.estimate(app, "EUR") == 0, "nothing left to search"
    again = await trip.search_trip(app, plan, rates, "EUR", limit=20)
    assert sorted(calls) == ["flights", "stays"]
    assert again["flights"]["from_memory"] is True and again["flights"]["search_id"] == first["flights"]["search_id"]
    await trip.search_trip(app, plan, rates, "EUR", refresh=True)
    assert len(calls) == 4, "refresh always searches"


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

    assert estimate_flights(query, [source], Limiter(), "") == 80.0, "two routes, four usual requests each"


async def test_a_one_way_trip_has_a_stay_only_when_a_last_day_is_given(app, located):
    gone = await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22")
    assert gone.flights.return_ is None and gone.stays is None
    settled = await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", checkout="2026-10-25")
    assert settled.flights.return_ is None and settled.stays.nights == 3
    longer = await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", "2026-10-29", checkout="2026-10-25")
    assert longer.flights.return_ == date(2026, 10, 29) and longer.stays.checkout == date(2026, 10, 25)


async def test_children_are_counted_as_each_side_counts_them(app, located):
    plan = await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", "2026-10-23", adults=2, children_ages=[1, 7, 14])
    assert (plan.flights.adults, plan.flights.children, plan.flights.infants) == (3, 1, 1), "an airline's adult is 12+"
    assert (plan.stays.adults, plan.stays.children, plan.stays.children_ages) == (2, 3, (1, 7, 14))
    with pytest.raises(ValueError, match="ages from 0 to 17"):
        await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", "2026-10-23", children_ages=[30])


async def test_separate_tickets_search_each_way_and_pair_them(app, located, monkeypatch):
    from datetime import datetime, timezone
    from travelops.core.common import Link
    from travelops.core.flights import Baggage, Fare, FlightOffer, Itinerary, Segment, at_airport
    from travelops.core.money import Money
    from travelops.merge.flights import merge_flights

    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    asked = []

    def offer(flight, origin, destination, day, hour, price, back=None):
        def seg(f, a, b, d, h):
            return Segment(
                f[:2], f, a, b, at_airport(f"2026-10-{d}T{h:02d}:00", a), at_airport(f"2026-10-{d}T{h + 1:02d}:00", b)
            )

        legs = ((seg(flight, origin, destination, day, hour),), (seg(*back),) if back else ())
        fare = Fare(
            Money(price, "EUR"),
            "seller",
            "tutu",
            "economy",
            Baggage(),
            Link("https://t.example/" + flight, "ticket"),
            now,
        )
        return FlightOffer(Itinerary(*legs), fare)

    async def flights(query, sources, ctx, rates, currency, **kwargs):
        asked.append((query.origins, query.destinations, query.return_, kwargs.get("sharing")))
        if query.return_:
            found = [offer("JU1", "BEG", "TGD", 22, 8, 150, back=("JU2", "TGD", "BEG", 23, 20))]
        elif query.origins == ("BEG",):
            found = [offer("JU1", "BEG", "TGD", 22, 8, 60), offer("4O5", "BEG", "TIV", 22, 10, 40)]
        else:
            found = [offer("JU2", "TGD", "BEG", 23, 20, 55), offer("4O6", "TIV", "BEG", 23, 7, 70)]
        return FlightSearch(query, currency, merge_flights(found, rates, currency), [])

    async def stays(query, sources, ctx, rates, currency, **kwargs):
        return StaySearch(query, currency, [], [])

    monkeypatch.setattr(recall, "search_flights", flights)
    monkeypatch.setattr(recall, "search_stays", stays)
    plan = await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", "2026-10-23", separate=True)
    assert plan.estimate(app, "EUR") > 0
    result = await trip.search_trip(app, plan, Rates("EUR", {}, "d"), "EUR")
    assert {(a[0], a[1], str(a[2])) for a in asked} == {
        (("BEG",), ("TIV", "TGD"), "2026-10-23"),
        (("BEG",), ("TIV", "TGD"), "None"),
        (("TIV", "TGD"), ("BEG",), "None"),
    }
    assert {a[3] for a in asked} == {3}, "three searches share the queues of the sources"
    block = result["separate_tickets"]
    best = block["pairs"][0]
    assert best["total"] == {"amount": "95.00", "currency": "EUR"} and best["open_jaw"] is True
    assert (best["outbound"]["flights"], best["return"]["flights"]) == (["4O5"], ["JU2"])
    assert best["outbound"]["link"]["url"] == "https://t.example/4O5" and "Two separate tickets" in block["risk"]
    assert block["cheapest_round_trip"]["amount"] == "150.00" and block["separate_is_cheaper_by"]["amount"] == "55.00"
    assert block["pairs_possible"] == 4 and block["outbound_search_id"] != block["return_search_id"]
    assert [p["open_jaw"] for p in block["pairs"]] == [True, False, False, True]


async def test_shifted_dates_do_not_move_the_stay(app, located, monkeypatch):
    async def flights(query, sources, ctx, rates, currency, **kwargs):
        return FlightSearch(query, currency, [], [])

    async def stays(query, sources, ctx, rates, currency, **kwargs):
        return StaySearch(query, currency, [], [])

    monkeypatch.setattr(recall, "search_flights", flights)
    monkeypatch.setattr(recall, "search_stays", stays)
    plan = await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22", "2026-10-23", flex_days=1)
    assert len(plan.flights.date_pairs()) == 3 and plan.stays.checkin == date(2026, 10, 22)
    result = await trip.search_trip(app, plan, Rates("EUR", {}, "d"), "EUR")
    assert any("asked dates only" in line for line in result["not_included"])
    bare = await trip.search_trip(
        app, await trip.plan_trip(app, "BEG", "Kotor", "2026-10-22"), Rates("EUR", {}, "d"), "EUR"
    )
    assert bare["stays"] is None and any("pass `checkout`" in line for line in bare["not_included"])
