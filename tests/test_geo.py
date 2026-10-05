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
