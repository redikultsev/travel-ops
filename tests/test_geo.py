from travelops.geo import airports_near, distance_km, parse_places

FEED = {
    "results": [
        {
            "name": "Kotor",
            "country": "Montenegro",
            "country_code": "ME",
            "admin1": "Kotor",
            "latitude": 42.421,
            "longitude": 18.768,
        },
        {
            "name": "Kotor",
            "country": "Bosnia and Herzegovina",
            "country_code": "BA",
            "latitude": 44.613,
            "longitude": 17.367,
        },
    ]
}


def test_places_keep_the_geocoder_order_and_a_country_narrows_them():
    places = parse_places(FEED)
    assert [p.country_code for p in places] == ["ME", "BA"] and places[0].label() == "Kotor, Montenegro"
    assert [p.country_code for p in parse_places(FEED, "ba")] == ["BA"]
    assert [p.country_code for p in parse_places(FEED, "Montenegro")] == ["ME"]
    assert parse_places({}) == []


def test_a_town_without_an_airport_gets_the_ones_that_serve_it():
    near = airports_near(42.421, 18.768, radius_km=60)
    assert [a["iata"] for a in near] == ["TIV", "TGD", "DBV"]
    assert near[0]["km_straight"] < 10 and near[2]["country_code"] == "HR"
    assert airports_near(42.421, 18.768, radius_km=60, limit=1) == near[:1]


def test_distance():
    assert round(distance_km(44.82, 20.31, 42.40, 18.72)) in range(290, 310)  # roughly BEG to TIV


async def test_the_drive_from_each_airport_comes_from_one_routing_request_and_failure_costs_only_it():
    import json as jsonlib

    from travelops.geo import airports_near, with_roads
    from travelops.net.client import Response

    class Net:
        calls = []

        def __init__(self, answer):
            self.answer = answer

        async def request(self, source, method, url, **kw):
            self.calls.append((source, url, kw))
            if isinstance(self.answer, Exception):
                raise self.answer
            return Response(200, jsonlib.dumps(self.answer).encode())

    near = airports_near(42.42067, 18.76825)
    table = {"code": "Ok", "distances": [[6600.0], [75000.0], [None]], "durations": [[540.0], [5700.0], [None]]}
    airports, credit = await with_roads(Net(table), 42.42067, 18.76825, [dict(a) for a in near])
    source, url, kw = Net.calls[-1]
    assert source == "routing" and url.startswith(
        "https://routing.openstreetmap.de/routed-car/table/v1/driving/18.76825,42.42067;"
    )
    assert kw["params"]["destinations"] == "0" and kw["cache_ttl"] > 0 and "travel-ops" in kw["headers"]["user-agent"]
    assert [(a["iata"], a.get("km_road"), a.get("minutes_road")) for a in airports] == [
        ("TIV", 7, 9),
        ("TGD", 75, 95),
        ("DBV", None, None),
    ], "a route the service could not find stays without a drive"
    assert "OpenStreetMap" in credit
    kept, note = await with_roads(Net(TimeoutError("slow")), 42.42067, 18.76825, [dict(a) for a in near])
    assert all("km_road" not in a for a in kept) and "unavailable" in note and kept[0]["km_straight"] == 4


def test_a_country_is_found_as_people_write_it():
    from travelops.geo import Place, in_country

    turkey = Place("Istanbul", "Republic of Türkiye", "TR", None, 41.0, 29.0)
    assert all(in_country(turkey, c) for c in ("Turkey", "TR", "tr", "Türkiye", "Turkiye", "Republic of Türkiye"))
    assert not in_country(turkey, "Tur"), "a fragment is not a country"
    bosnia = Place("Kotor", "Bosnia and Herzegovina", "BA", None, 44.6, 17.4)
    assert in_country(bosnia, "Bosnia") and not in_country(bosnia, "Montenegro")


def test_a_place_is_labelled_the_way_a_search_box_reads_it():
    from travelops.geo import Place

    assert Place("Istanbul", "Republic of Türkiye", "TR", None, 41.0, 29.0).label() == "Istanbul, Türkiye"
    assert Place("Kotor", "Montenegro", "ME", None, 42.4, 18.8).label() == "Kotor, Montenegro"


def test_only_airports_with_scheduled_flights_serve_a_place():
    from travelops.geo import airports_near

    istanbul = [a["iata"] for a in airports_near(41.01, 28.98, 100)]
    assert istanbul[:2] == ["SAW", "IST"] and "ISL" not in istanbul, "Atatürk is closed to passengers"
    tokyo = [a["iata"] for a in airports_near(35.68, 139.76, 100)]
    assert tokyo[:2] == ["HND", "NRT"] and not {"NJA", "OKO"} & set(tokyo), "air bases sell no tickets"
