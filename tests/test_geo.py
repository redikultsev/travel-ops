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


FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"
# Live answers of 2026-10-10, and a landmark of each historic centre to measure from.
LANDMARKS = {
    "sarajevo": ("Bosnia and Herzegovina", (43.8597, 18.4313)),  # Baščaršija
    "belgrade": ("Serbia", (44.8163, 20.4602)),  # Republic Square
    "istanbul": ("Türkiye", (41.0055, 28.9768)),  # Sultanahmet
    "novi-sad": ("Serbia", (45.2551, 19.8451)),  # Trg slobode
}


def fixture(source, city) -> bytes:
    return (FIXTURES / source / f"{city}.json").read_bytes()


def located(city):
    import json as jsonlib

    return parse_places(jsonlib.loads(fixture("geocoder", city)), LANDMARKS[city][0])[0]


class Answers:
    """A network that answers each source with its body, its status, or an exception, and remembers the calls."""

    def __init__(self, **answers):
        self.answers, self.calls = answers, []

    async def request(self, source, method, url, **kw):
        from travelops.net.client import Response

        self.calls.append((source, url, kw))
        answer = self.answers[source]
        if isinstance(answer, Exception):
            raise answer
        status, body = answer if isinstance(answer, tuple) else (200, answer)
        return Response(status, body)


def test_the_geocoder_id_is_kept_but_not_shown():
    from travelops.geo import place_json

    place = located("sarajevo")
    assert place.geonames_id == 3191281 and (place.lat, place.lon) == (43.84864, 18.35644)
    assert "geonames_id" not in place_json(place)


async def test_the_centre_is_openstreetmaps_place_node_of_the_towns_wikidata_item():
    from travelops.geo import centre

    for city, (_, landmark) in LANDMARKS.items():
        place = located(city)
        net = Answers(wikidata=fixture("wikidata", city), nominatim=fixture("nominatim", city))
        found = await centre(net, place)
        assert found["from"] == "openstreetmap" and "OpenStreetMap contributors" in found["credit"], city
        assert distance_km(*landmark, found["lat"], found["lon"]) < 1.6, city
        assert [source for source, _, _ in net.calls] == ["wikidata", "nominatim"]
        (_, _, asked), (_, _, searched) = net.calls
        assert asked["params"]["gsrsearch"] == f"haswbstatement:P1566={place.geonames_id}"
        assert searched["params"]["countrycodes"] == place.country_code.lower() and searched["params"]["extratags"]
        assert all(kw["cache_ttl"] >= 86400 and "travel-ops" in kw["headers"]["user-agent"] for _, _, kw in net.calls)
    sarajevo = await centre(
        Answers(wikidata=fixture("wikidata", "sarajevo"), nominatim=fixture("nominatim", "sarajevo")),
        located("sarajevo"),
    )
    assert (
        distance_km(*LANDMARKS["sarajevo"][1], 43.84864, 18.35644)
        > 6
        > 1.6
        > distance_km(*LANDMARKS["sarajevo"][1], sarajevo["lat"], sarajevo["lon"])
    ), "the geocoder's point was 6 km out"


def test_a_boundary_stands_for_the_town_only_through_its_linked_place_node():
    import json as jsonlib

    from travelops.geo import parse_place_node

    belgrade = jsonlib.loads(fixture("nominatim", "belgrade"))
    assert belgrade[0]["category"] == "boundary" and "linked_place" not in belgrade[0]["extratags"]
    assert parse_place_node(belgrade, "Q3711", located("belgrade")) == (
        44.8178131,
        20.4568974,
    ), "the node, not the area"
    assert parse_place_node(belgrade[:1], "Q3711", located("belgrade")) is None
    novi_sad = jsonlib.loads(fixture("nominatim", "novi-sad"))
    assert parse_place_node(novi_sad, "Q55630", located("novi-sad")) == (45.2551338, 19.8451756)
    assert parse_place_node(novi_sad, "Q3711", located("novi-sad")) is None, "another item's node is not this town's"


async def test_each_step_that_fails_leaves_the_one_before():
    import json as jsonlib

    from travelops.geo import Place, centre

    place = located("sarajevo")
    geonames = {"name": "Sarajevo, Bosnia and Herzegovina", "lat": 43.84864, "lon": 18.35644, "from": "geonames"}
    item = fixture("wikidata", "sarajevo")
    page = jsonlib.loads(item)["query"]["pages"][0]

    def pages(*found):
        return jsonlib.dumps({"query": {"pages": list(found)}}).encode()

    wikidata = {**geonames, "lat": 43.85638889, "lon": 18.41305556, "from": "wikidata"}
    for nominatim in (TimeoutError("slow"), (502, b"Bad Gateway"), b"[]", fixture("nominatim", "belgrade")):
        assert await centre(Answers(wikidata=item, nominatim=nominatim), place) == wikidata
    far = dict(page, coordinates=[dict(page["coordinates"][0], lat=44.8, lon=20.46)])  # Belgrade's point
    assert (await centre(Answers(wikidata=pages(far), nominatim=b"[]"), place)) == geonames
    assert (await centre(Answers(wikidata=pages(far), nominatim=fixture("nominatim", "sarajevo")), place))[
        "from"
    ] == "openstreetmap", "an item placed elsewhere still names the town's node"
    for wikidata_answer in (
        pages(page, dict(page, title="Q1")),
        b'{"batchcomplete": true}',
        b'{"error": {"code": "maxlag"}}',
        (502, b"<html>Bad Gateway</html>"),
        TimeoutError("slow"),
    ):
        net = Answers(wikidata=wikidata_answer, nominatim=fixture("nominatim", "sarajevo"))
        assert await centre(net, place) == geonames and [c[0] for c in net.calls] == ["wikidata"]
    no_id = Place("Kotor", "Montenegro", "ME", None, 42.421, 18.768)
    net = Answers()
    assert (await centre(net, no_id))["from"] == "geonames" and net.calls == []


async def test_an_answer_not_worth_reusing_is_asked_again(tmp_path):
    from travelops.net.client import Net, Response
    from travelops.net.limiter import Limiter, Rule

    sent = []

    class Counting(Net):
        async def _send(self, method, url, **kw):
            sent.append(url)
            return Response(200, b'{"error": {"code": "maxlag"}}' if len(sent) == 1 else b'{"ok": 1}')

    async def no_wait(seconds):
        return None

    net = Counting(tmp_path, limiter=Limiter(None, default=Rule(interval=0, jitter=0), sleep=no_wait))
    ask = dict(cache_ttl=3600, reuse_if=lambda r: b'"error"' not in r.body)
    assert b"maxlag" in (await net.request("wikidata", "GET", "https://example.test/w", **ask)).body
    assert (await net.request("wikidata", "GET", "https://example.test/w", **ask)).json() == {"ok": 1}
    assert (await net.request("wikidata", "GET", "https://example.test/w", **ask)).json() == {"ok": 1}
    assert len(sent) == 2, "the good answer is served from the cache"
