from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from travelops.core.stays import StayQuery
from travelops.net.client import Response
from travelops.sources.base import Context, NotConfigured
from travelops.sources.stays.airbnb import Source

QUERY = StayQuery("Belgrade", date(2026, 11, 14), date(2026, 11, 16))
NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


async def test_ssr_pages_follow_the_cursors_without_a_browser():
    page = b'<html>"paginationInfo":{"pageCursors":["c0","c1","c2"]}</html>'

    class Net:
        calls = []

        async def request(self, *args, **kw):
            self.calls.append((args, kw))
            return Response(200, page)

    net = Net()
    raws = await Source().fetch(QUERY, Context(net, None, lambda: NOW))
    assert len(raws) == len(net.calls) == 3 and Source().max_requests(QUERY) == 8
    args, kw = net.calls[0]
    assert args[1] == "GET" and args[2] == "https://www.airbnb.com/s/Belgrade/homes" and "cursor" not in kw["params"]
    assert kw["params"]["checkin"] == "2026-11-14" and kw["params"]["adults"] == 2
    assert [kw["params"].get("cursor") for _, kw in net.calls[1:]] == ["c1", "c2"], "the first cursor is page one"
    assert "ne_lat" not in kw["params"], "no centre, no box"


async def test_with_the_place_centre_known_the_map_box_names_the_place():
    class Net:
        params = None

        async def request(self, *args, **kw):
            self.params = kw["params"]
            return Response(200, b"<html></html>")

    net = Net()
    from dataclasses import replace

    await Source().fetch(replace(QUERY, center=(44.8, 20.47)), Context(net, None, lambda: NOW))
    p = net.params
    assert p["sw_lat"] < 44.8 < p["ne_lat"] and p["sw_lng"] < 20.47 < p["ne_lng"] and p["search_by_map"] == "true"
    assert round(p["ne_lat"] - 44.8, 2) == 0.14, "15 km each way"


def test_a_monthly_price_is_not_taken_for_the_stays_total():
    import json

    card = {
        "__typename": "StaySearchResult",
        "demandStayListing": {"id": "RGVtYW5kU3RheUxpc3Rpbmc6MTIz", "description": {"name": {
            "localizedStringWithTranslationPreference": "Flat"}}},
        "structuredDisplayPrice": {
            "primaryLine": {"discountedPrice": "€5,075", "qualifier": "monthly"},
            "displayPriceStyle": "MONTHLY",
        },
    }
    data = {"niobeClientData": [["k", {"data": {"presentation": {"staysSearch": {"results": {
        "searchResults": [card], "paginationInfo": {}}}}}}]]}
    page = ('<html><script id="data-deferred-state-0" type="application/json">' + json.dumps(data)
            + "</script></html>").encode()
    result = Source().parse([page], QUERY, NOW)
    assert result.offers == [] and "monthly price" in result.notes[0]


async def test_multiple_rooms_are_not_representable():
    with pytest.raises(NotConfigured):
        await Source().fetch(
            StayQuery(QUERY.place, QUERY.checkin, QUERY.checkout, rooms=2), Context(None, None, lambda: NOW)
        )


def recorded():
    from pathlib import Path

    return (Path(__file__).parents[1] / "fixtures/airbnb/belgrade-2026-11-14-2026-11-16-0.html").read_bytes()


def test_recorded_total_discount_coordinates_rating_and_links():
    from travelops.core.money import Money

    result = Source().parse([recorded()], QUERY, NOW)
    assert len(result.offers) == 18
    first = result.offers[0]
    assert first.stay.source_id == "947778502425423294"
    assert first.stay.name == "Great location for a great price"
    assert (first.stay.lat, first.stay.lon) == (44.8031, 20.4837)
    assert first.stay.rating == 10.0 and first.stay.reviews == 173
    assert first.stay.photos[0] == "https://a0.muscache.com/im/pictures/54f8b7b4-d492-43ca-a6be-dca2d52bb590.jpg"
    assert first.rate.total == Money(80, "EUR")
    assert first.rate.link.kind == "property" and "check_in=2026-11-14" in first.rate.link.url
    assert any(n.startswith("18 listings from 1 pages of 18") for n in result.notes)


def test_empty_missing_state_domain_handoff_and_nightly_only():
    import json
    from travelops.sources.base import ParseError
    from travelops.sources.stays._html import Tree

    empty = {"niobeClientData": [{"staysSearch": {"results": {"searchResults": []}}}]}
    raw = ('<script id="data-deferred-state-0">' + json.dumps(empty) + "</script>").encode()
    assert Source().parse([raw], QUERY, NOW).offers == []
    with pytest.raises(ParseError, match="handoff"):
        Source().parse([b'<form method="POST" action="https://www.airbnb.rs/"></form>'], QUERY, NOW)
    with pytest.raises(ParseError):
        Source().parse([b"<html>unrelated</html>"], QUERY, NOW)
    data = json.loads(Tree(recorded()).root.find(id="data-deferred-state-0").text())

    def replace_prices(x):
        if isinstance(x, dict):
            if x.get("__typename") == "StaySearchResult":
                x["structuredDisplayPrice"] = {"primaryLine": {"price": "€40", "qualifier": "per night"}}
            for v in x.values():
                replace_prices(v)
        elif isinstance(x, list):
            for v in x:
                replace_prices(v)

    replace_prices(data)
    raw = ('<script id="data-deferred-state-0">' + json.dumps(data) + "</script>").encode()
    with pytest.raises(ParseError, match="total"):
        Source().parse([raw], QUERY, NOW)


@pytest.mark.live
async def test_live_search():
    from tests.live import check_source

    offers = await check_source(Source(), QUERY)
    assert offers[0].rate.link.kind == "property"


def test_a_card_says_what_kind_of_place_it_is_and_whether_it_cancels_free():
    import json
    import re

    page = recorded().decode()
    state = re.search(r'(<script[^>]*id="data-deferred-state-0"[^>]*>)(.*?)(</script>)', page, re.S)
    data = json.loads(state[2])

    def items(node):
        if isinstance(node, dict):
            if isinstance(node.get("staysSearch"), dict):
                yield from node["staysSearch"]["results"]["searchResults"]
            for value in node.values():
                yield from items(value)
        elif isinstance(node, list):
            for value in node:
                yield from items(value)

    found = list(items(data))
    # As a live card carries them; the recorded page was cut down to the fields the first parser read.
    found[0].update(
        title="Shared room in Belgrade",
        structuredContent={"mapPrimaryLine": [{"body": "1 bed", "type": "BEDINFO"}, {"body": "1 shared bath"}]},
        paymentMessages=[{"type": "FREE_CANCELLATION_HIGHLIGHT", "text": "Free cancellation"}],
    )
    found[1].update(title="Apartment in Belgrade")
    offers = (
        Source().parse([(page[: state.start(2)] + json.dumps(data) + page[state.end(2) :]).encode()], QUERY, NOW).offers
    )
    assert (
        offers[0].stay.kind == "shared_room" and offers[0].rate.room == "Shared room in Belgrade, 1 bed, 1 shared bath"
    )
    assert offers[0].rate.free_cancellation is True
    assert offers[1].stay.kind == "apartment" and offers[1].rate.free_cancellation is None
    assert offers[2].stay.kind == "other" and offers[2].rate.room is None


def test_a_listing_page_gives_amenities_place_and_what_the_place_really_is():
    from travelops.sources.base import ParseError

    page = (Path(__file__).parents[1] / "fixtures/airbnb/room-kotor.html").read_bytes()
    details = Source().parse_details(page)
    assert details["kind"] == "shared_room" and details["kind_as_listed"] == "Shared room in rental unit"
    assert "Wifi" in details["amenities"] and "Smoke alarm" in details["not_available"]
    assert "Smoke alarm" not in details["amenities"], "what the page marks as absent is not an amenity"
    assert (details["lat"], details["lon"]) == (42.4253, 18.7702)
    assert len(details["photos"]) == 4 and all("icon" not in url for url in details["photos"])
    with pytest.raises(ParseError):
        Source().parse_details(b"<html>unrelated</html>")


def test_one_card_with_a_nightly_rate_only_is_left_out_not_the_answer():
    import json
    from travelops.sources.stays._html import Tree

    data = json.loads(Tree(recorded()).root.find(id="data-deferred-state-0").text())
    whole = len(Source().parse([recorded()], QUERY, NOW).offers)
    first = []

    def strip_first(x):
        if isinstance(x, dict):
            if x.get("__typename") == "StaySearchResult" and not first:
                first.append(x)
                x["structuredDisplayPrice"] = {"primaryLine": {"price": "€40", "qualifier": "per night"}}
            for v in x.values():
                strip_first(v)
        elif isinstance(x, list):
            for v in x:
                strip_first(v)

    strip_first(data)
    raw = ('<script id="data-deferred-state-0">' + json.dumps(data) + "</script>").encode()
    parsed = Source().parse([raw], QUERY, NOW)
    assert len(parsed.offers) == whole - 1
    assert "1 listings showed a nightly rate only and were left out" in parsed.notes


async def test_the_pages_after_the_first_are_read_under_the_cheapest_quarter():
    from dataclasses import replace

    from travelops.sources.stays import airbnb

    first = recorded()

    class Net:
        calls = []

        async def request(self, *args, **kw):
            self.calls.append(kw["params"])
            return Response(200, first)

    net = Net()
    source = Source()
    raws = await source.fetch(QUERY, Context(net, None, lambda: NOW))
    totals = sorted(float(o.rate.total.amount) for o in Source().parse([first], QUERY, NOW).offers)
    assert "price_max" not in net.calls[0], "the first page as Airbnb ranks it"
    assert net.calls[1]["price_max"] == airbnb.band(totals) and net.calls[1]["price_filter_input_type"] == 2
    assert all(c["price_max"] == net.calls[1]["price_max"] for c in net.calls[1:]) and len(raws) == len(net.calls)
    assert len(net.calls) <= airbnb.PAGES
    assert "cheapest quarter" in source.parse(raws, QUERY, NOW).notes[0]
    asked = replace(QUERY, max_night_eur=40.0)
    net.calls = []
    await Source().fetch(asked, Context(net, None, lambda: NOW))
    assert {c["price_max"] for c in net.calls} == {80}, "the asked ceiling, two nights, on every page"


async def test_a_ceiling_with_more_listings_than_pages_left_is_lowered_once():
    from travelops.sources.stays import airbnb

    first = recorded()
    crowded = first + b'"searchButtonText":"Show 500 places"'

    class Net:
        calls = []

        async def request(self, *args, **kw):
            self.calls.append(kw["params"])
            return Response(200, crowded if "price_max" in kw["params"] else first)

    net = Net()
    await Source().fetch(QUERY, Context(net, None, lambda: NOW))
    caps = [c.get("price_max") for c in net.calls]
    assert caps[0] is None and caps[2] < caps[1], "lowered after the count"
    assert set(caps[3:]) <= {caps[2]} and len(net.calls) <= airbnb.PAGES
    assert airbnb.places(b'"searchButtonText":"Show 1,000+ places"') == 1000


async def test_under_an_asked_ceiling_everything_is_read_and_set_against_airbnbs_count():
    from dataclasses import replace

    page = recorded() + b'"searchButtonText":"Show 18 places""pageCursors":["c0"]'

    class Net:
        calls = []

        async def request(self, *args, **kw):
            self.calls.append(kw["params"])
            return Response(200, page)

    net = Net()
    source = Source()
    asked = replace(QUERY, max_night_eur=200.0)
    raws = await source.fetch(asked, Context(net, None, lambda: NOW))
    parsed = source.parse(raws, asked, NOW)
    assert [c["price_max"] for c in net.calls] == [400] and "price_min" not in net.calls[0]
    assert parsed.coverage.complete and (parsed.coverage.read, parsed.coverage.counted) == (18, 18)
    assert parsed.coverage.ceiling_eur == 400.0 and "everything Airbnb has under 400 EUR" in parsed.notes[0]


async def test_a_band_with_more_than_airbnb_pages_through_is_halved_and_short_reads_are_not_complete():
    from dataclasses import replace

    base = recorded()

    def page(count, cursors):
        names = ",".join(f'"c{i}"' for i in range(cursors))
        return base + f'"searchButtonText":"Show {count} places""pageCursors":[{names}]'.encode()

    class Net:
        calls = []

        async def request(self, *args, **kw):
            p = kw["params"]
            self.calls.append(p)
            if p.get("price_min", 0) == 0 and p["price_max"] == 400:
                return Response(200, page("1,000+", 15))
            return Response(200, page(40, 3))

    net = Net()
    source = Source()
    asked = replace(QUERY, max_night_eur=200.0)
    raws = await source.fetch(asked, Context(net, None, lambda: NOW))
    bands = [(c.get("price_min", 0), c["price_max"]) for c in net.calls if "cursor" not in c]
    assert bands == [(0, 400), (0, 200), (200, 400)], "halved, the cheaper half first, meeting at the edge"
    parsed = source.parse(raws, asked, NOW)
    assert parsed.coverage.counted == 80 and parsed.coverage.read == 18 and not parsed.coverage.complete
    assert "not all of it" in parsed.notes[0]
