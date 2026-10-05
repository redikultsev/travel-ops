from datetime import date, datetime, timezone
import pytest
from travelops.core.stays import StayQuery
from travelops.net.client import Response
from travelops.sources.base import Context, NotConfigured
from travelops.sources.stays.airbnb import Source

QUERY = StayQuery("Belgrade", date(2026, 11, 14), date(2026, 11, 16))
NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


async def test_single_ssr_get_without_browser_or_details():
    class Net:
        calls = []

        async def request(self, *args, **kw):
            self.calls.append((args, kw))
            return Response(200, b"<html></html>")

    net = Net()
    await Source().fetch(QUERY, Context(net, None, lambda: NOW))
    assert len(net.calls) == Source().max_requests(QUERY) == 1
    args, kw = net.calls[0]
    assert args[1] == "GET" and args[2] == "https://www.airbnb.com/s/Belgrade/homes"
    assert kw["params"]["checkin"] == "2026-11-14" and kw["params"]["adults"] == 2


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
    assert any("first SSR page: 18" in n for n in result.notes)


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
