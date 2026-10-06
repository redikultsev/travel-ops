import pytest

from travelops.memory import Results
from travelops.views import flights_view, stays_view


def fare(amount, seller="s", currency="EUR", converted=True):
    money = {"amount": str(amount), "currency": currency}
    return {
        "price": money,
        "converted": {"amount": str(amount), "currency": "EUR"} if converted else None,
        "seller": seller,
        "link": None,
        "seen_at": "2026-10-05T10:00:00+00:00",
    }


def seg(flight, origin, destination, departs):
    return {
        "carrier": flight[:2],
        "flight": flight,
        "origin": origin,
        "destination": destination,
        "departs": f"2026-10-22T{departs}:00+02:00",
        "arrives": f"2026-10-22T{departs}:00+02:00",
        "operating": flight[:2],
    }


def card(out, back, groups, stops=0, minutes=60):
    return {
        "outbound": out,
        "inbound": back,
        "stops": stops,
        "duration_min": minutes,
        "return_duration_min": minutes if back else None,
        "groups": [{"cabin": "economy", "checked_bag": bag, "fares": fares} for bag, fares in groups],
    }


def flights():
    return {
        "currency": "EUR",
        "cards": [
            card([seg("JU170", "BEG", "TIV", "07:10")], [seg("JU171", "TIV", "BEG", "07:30")], [(False, [fare(100)])]),
            card(
                [seg("JU172", "BEG", "TIV", "18:40")],
                [seg("JU173", "TIV", "BEG", "20:05")],
                [(False, [fare(120)]), (True, [fare(150), fare(170)])],
            ),
            card(
                [seg("4O100", "BEG", "TGD", "12:00")],
                [seg("4O101", "TGD", "BEG", "21:00")],
                [(None, [fare(130)])],
                minutes=45,
            ),
            card(
                [seg("TK1", "BEG", "IST", "06:00"), seg("TK2", "IST", "TGD", "12:00")],
                [seg("TK3", "TGD", "BEG", "09:00")],
                [(True, [fare(90)])],
                stops=1,
                minutes=400,
            ),
        ],
    }


def test_each_filter_says_what_it_hid():
    view = flights_view(flights(), max_stops=0, depart_after="12:00", return_after="20:00")
    assert [c["outbound"][0]["flight"] for c in view["cards"]] == ["JU172", "4O100"]
    assert view["filtered"] == {
        "hidden_cards": 2,
        "by": [
            {"filter": "max_stops", "value": 0, "hidden": 1},
            {"filter": "depart_after", "value": "12:00", "hidden": 1},
            {"filter": "return_after", "value": "20:00", "hidden": 0},
        ],
    }
    assert view["shown"]["of"] == 2 and view["shown"]["sorted_by"] == "price"


def test_a_journey_that_waits_for_days_is_hidden_with_a_count():
    view = flights_view(flights(), max_leg_hours=6)
    assert [c["outbound"][0]["flight"] for c in view["cards"]] == ["JU170", "JU172", "4O100"]
    assert view["filtered"]["by"] == [{"filter": "max_leg_hours", "value": 6, "hidden": 1}]


def test_airline_and_airport_filters():
    assert len(flights_view(flights(), airlines=["ju"])["cards"]) == 2
    assert len(flights_view(flights(), avoid_airlines="JU,TK")["cards"]) == 1
    only_tgd = flights_view(flights(), destination="TGD")
    assert {c["outbound"][-1]["destination"] for c in only_tgd["cards"]} == {"TGD"}


def test_a_bag_filter_keeps_the_fares_with_a_bag_and_counts_the_unknown_apart():
    view = flights_view(flights(), checked_bag=True)
    assert [c["groups"][0]["fares"][0]["price"]["amount"] for c in view["cards"]] == ["90", "150"]
    assert all(len(c["groups"]) == 1 for c in view["cards"])
    assert view["filtered"]["by"] == [{"filter": "checked_bag", "value": True, "hidden": 1, "hidden_unknown": 1}]


def test_a_price_ceiling_drops_dearer_fares_and_does_not_guess_other_currencies():
    data = flights()
    data["cards"].append(
        card(
            [seg("SU1", "BEG", "TIV", "10:00")],
            [seg("SU2", "TIV", "BEG", "11:00")],
            [(False, [fare(50, "ru", "RUB", False)])],
        )
    )
    view = flights_view(data, max_price=125)
    assert [c["groups"][0]["fares"][0]["price"]["amount"] for c in view["cards"]] == ["90", "100", "120"]
    assert len(view["cards"][2]["groups"]) == 1, "the dearer fare with a bag is gone, the card stays"
    assert view["filtered"]["by"] == [{"filter": "max_price", "value": 125, "hidden": 1, "hidden_unknown": 1}]


def test_other_orders_and_leg_options_follow_the_filters():
    quick = flights_view(flights(), sort="duration")
    assert quick["cards"][0]["outbound"][0]["flight"] == "4O100" and quick["shown"]["sorted_by"] == "duration"
    early = flights_view(flights(), sort="departure", limit=1)
    assert early["cards"][0]["outbound"][0]["flight"] == "TK1" and early["shown"] == {
        "cards": 1,
        "of": 4,
        "offers_per_group_at_most": 5,
        "sorted_by": "departure",
    }
    evening = flights_view(flights(), return_after="20:00")
    assert [o["flights"] for o in evening["return_options"]] == [["JU173"], ["4O101"]]


def test_bad_filters_are_refused():
    for bad in ({"depart_after": "7"}, {"sort": "best"}, {"max_price": -1}, {"checked_bag": False}, {"airlines": []}):
        with pytest.raises(ValueError):
            flights_view(flights(), **bad)
    one_way = flights()
    for c in one_way["cards"]:
        c["inbound"] = []
    with pytest.raises(ValueError, match="no return"):
        flights_view(one_way, return_after="10:00")


def stays():
    def stay(name, kind, rating, total, source="booking", reviews=10):
        return {
            "stay": {
                "source": source,
                "source_id": name,
                "name": name,
                "kind": kind,
                "rating": rating,
                "reviews": reviews,
                "photos": [],
                "amenities": [],
            },
            "rates": [{"total": {"amount": str(total), "currency": "EUR"}, "converted": None}],
        }

    return {
        "currency": "EUR",
        "cards": [
            stay("dorm", "shared_room", 9.0, 16),
            stay("room", "other", 8.4, 29, reviews=300),
            stay("flat", "apartment", None, 35, source="airbnb"),
            stay("hotel", "hotel", 9.6, 80),
        ],
    }


def test_stay_filters_and_orders():
    private = stays_view(stays(), exclude_kinds=["shared_room"], min_rating=8.0, max_total=50)
    assert [c["stay"]["name"] for c in private["cards"]] == ["room"]
    assert private["filtered"] == {
        "hidden_cards": 3,
        "by": [
            {"filter": "min_rating", "value": 8.0, "hidden": 0, "hidden_unknown": 1},
            {"filter": "exclude_kinds", "value": ["shared_room"], "hidden": 1},
            {"filter": "max_total", "value": 50, "hidden": 1, "kept_without_stated_taxes": 1},
        ],
    }
    assert [c["stay"]["name"] for c in stays_view(stays(), sort="rating")["cards"]] == ["hotel", "dorm", "room", "flat"]
    assert stays_view(stays(), sort="reviews")["cards"][0]["stay"]["name"] == "room"
    trusted = stays_view(stays(), min_reviews=100, sort="rating")
    assert [c["stay"]["name"] for c in trusted["cards"]] == ["room"]
    assert trusted["filtered"]["by"] == [{"filter": "min_reviews", "value": 100, "hidden": 3}]
    assert [c["stay"]["name"] for c in stays_view(stays(), sources=["airbnb"])["cards"]] == ["flat"]
    with pytest.raises(ValueError, match="kinds"):
        stays_view(stays(), kinds=["castle"])
    data = stays()
    data["cards"][1]["stay"]["name"] = "Old Town Hostel"  # a private room, in a hostel
    homes = stays_view(data, no_hostels=True, exclude_kinds=["shared_room"])
    assert [c["stay"]["name"] for c in homes["cards"]] == ["flat", "hotel"]
    assert {"filter": "no_hostels", "value": True, "hidden": 1} in homes["filtered"]["by"]


def test_memory_keeps_the_whole_result_and_forgets_after_a_week():
    now = [1_000.0]
    memory = Results(None, clock=lambda: now[0])
    key = memory.key("flights", {"origins": ["BEG"]}, ["tutu", "aviasales"], "EUR")
    assert key == memory.key("flights", {"origins": ["BEG"]}, ["aviasales", "tutu"], "EUR")
    stored = memory.put("flights", key, {"cards": [1, 2, 3]})
    assert stored.id.startswith("f") and len(stored.id) == 8 and stored.fresh
    stored.result["cards"].clear()
    assert memory.get(stored.id.upper()).result == {"cards": [1, 2, 3]}, "a view cannot damage what is kept"
    other = memory.key("flights", {"origins": ["BEG"]}, ["tutu", "aviasales", "kiwi"], "EUR")
    assert memory.recent(other) is None and memory.recent(other, any_sources=True).id == stored.id
    now[0] += 31 * 60
    assert memory.recent(key) is None and memory.recent(key, None).id == stored.id
    now[0] += 8 * 86400
    memory.put("stays", "other", {})
    assert memory.get(stored.id) is None


def test_the_shortlist_shows_the_cheapest_way_into_every_airport():
    cards = [
        card(
            [seg(f"4O{i}", "BEG", "TGD", "10:00")], [seg(f"4O9{i}", "TGD", "BEG", "20:00")], [(False, [fare(100 + i)])]
        )
        for i in range(6)
    ] + [card([seg("JU1", "BEG", "TIV", "10:00")], [seg("JU2", "TIV", "BEG", "07:30")], [(False, [fare(180)])])]
    view = flights_view({"currency": "EUR", "cards": cards}, limit=3)
    assert [c["outbound"][-1]["destination"] for c in view["cards"]] == ["TGD", "TGD", "TIV"]
    assert view["shown"]["of"] == 7


def test_the_next_town_is_hidden_and_a_stay_of_unknown_place_is_kept():
    data = stays()
    data["cards"][0]["stay"]["center_km"] = 21.9  # filed under the city, in the next town
    data["cards"][1]["stay"]["center_km"] = 0.6
    view = stays_view(data, max_center_km=15)
    assert [c["stay"]["name"] for c in view["cards"]] == ["room", "flat", "hotel"]
    assert view["filtered"]["by"] == [{"filter": "max_center_km", "value": 15, "hidden": 1, "kept_without_distance": 2}]


def test_a_long_wait_between_flights_is_hidden_with_a_count():
    view = flights_view(flights(), max_connection_hours=4)
    assert "TK1" not in [c["outbound"][0]["flight"] for c in view["cards"]], "06:00 to 12:00 in Istanbul is six hours"
    assert view["filtered"]["by"] == [{"filter": "max_connection_hours", "value": 4, "hidden": 1}]
