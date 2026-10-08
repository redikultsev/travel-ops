import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from travelops.core.flights import FlightQuery
from travelops.core.money import Money
from travelops.net.browser import Session
from travelops.net.client import Blocked, Response
from travelops.sources.base import Context, ParseError
from travelops.sources.flights.aviasales import Source, challenge, results_link

NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)
QUERY = FlightQuery(("BEG",), ("MOW",), date(2026, 11, 14))
# A search whose results page form was not observed goes over HTTP with the page's token.
HTTP_QUERY = FlightQuery(("BEG",), ("MOW",), date(2026, 11, 14), cabin="business")


class FakeNet:
    def __init__(self, bodies):
        self.bodies, self.calls = iter(bodies), []

    async def request(self, source, method, url, **kwargs):
        self.calls.append((source, method, url, kwargs))
        return Response(200, json.dumps(next(self.bodies)).encode())


class FakeBrowser:
    def __init__(self):
        self.calls, self.dropped = [], []

    async def get(self, source, url, **kwargs):
        self.calls.append((url, kwargs))
        return Session({"aws-waf-token": "fixture-token", "other": "x"}, "test-UA", "camoufox", 0)

    def drop(self, source):
        self.dropped.append(source)


FIXTURE = Path(__file__).parents[1] / "fixtures/aviasales/beg-mow-2026-11-14.json"
FINAL = [{"tickets": [], "flight_legs": [], "last_update_timestamp": 0}]


async def test_fetch_warms_on_the_results_page_and_sends_the_waf_token():
    net = FakeNet([{"search_id": "test-id", "results_url": "results.aviasales.ru"}, FINAL])
    browser = FakeBrowser()
    raw = await Source().fetch(HTTP_QUERY, Context(net, browser, lambda: NOW))
    assert len(raw) == 1 and len(net.calls) == 2
    url, kwargs = browser.calls[0]
    assert url == "https://www.aviasales.ru/search/BEG1411MOW1"
    assert kwargs["ready_cookie"] == "aws-waf-token" and kwargs["engine"] == "camoufox" and kwargs["max_age"] <= 300
    start = net.calls[0][3]
    assert start["headers"]["x-aws-waf-token"] == "fixture-token" and start["headers"]["x-client-type"] == "web"
    assert start["json"]["search_params"]["directions"][0]["date"] == "2026-11-14"
    assert start["json"]["search_params"]["passengers"]["adults"] == 1
    assert start["impersonate"] == "firefox"
    poll = net.calls[1][3]["json"]
    assert poll["price_per_person"] is False and poll["last_update_timestamp"] == 0
    wrapped = json.loads(raw[0])
    assert wrapped["complete"] is True and (wrapped["origin"], wrapped["destination"]) == ("BEG", "MOW")
    assert Source().max_requests(HTTP_QUERY) >= len(net.calls) + 1


async def test_fetch_keeps_every_answer_and_polls_from_the_last_stamp():
    partial = [{"tickets": [], "flight_legs": [], "last_update_timestamp": 77}]
    net = FakeNet([{"search_id": "test-id", "results_url": "results.aviasales.ru"}, partial, FINAL])
    raw = await Source().fetch(HTTP_QUERY, Context(net, FakeBrowser(), lambda: NOW))
    assert net.calls[2][3]["json"]["last_update_timestamp"] == 77
    assert len(json.loads(raw[0])["responses"]) == 2


async def test_fetch_rejects_an_unrelated_results_host():
    net = FakeNet([{"search_id": "test-id", "results_url": "example.com"}])
    with pytest.raises(ParseError, match="host"):
        await Source().fetch(HTTP_QUERY, Context(net, FakeBrowser(), lambda: NOW))
    assert len(net.calls) == 1


async def test_a_refusal_drops_the_stale_session():
    class Refusing(FakeNet):
        async def request(self, *args, **kwargs):
            raise Blocked("the site refused our address")

    browser = FakeBrowser()
    with pytest.raises(Blocked):
        await Source().fetch(HTTP_QUERY, Context(Refusing([]), browser, lambda: NOW))
    assert browser.dropped == ["aviasales"]


async def test_an_economy_search_is_read_from_the_results_pages_own_answers():
    asked = []

    class Browser:
        async def search(self, source, url, pattern, **kwargs):
            asked.append((url, pattern, kwargs))
            partial = [{"tickets": [], "flight_legs": [], "last_update_timestamp": 77}]
            bodies = [json.dumps(partial).encode(), b"", json.dumps(FINAL).encode()]
            assert kwargs["until"](bodies) and not kwargs["until"](bodies[:2]), "done at the final stamp"
            return bodies

    class NoNet:
        async def request(self, *args, **kwargs):
            raise AssertionError("nothing leaves the browser")

    raw = await Source().fetch(QUERY, Context(NoNet(), Browser(), lambda: NOW))
    url, pattern, kwargs = asked[0]
    assert url == "https://www.aviasales.ru/search/BEG1411MOW1" and "v3\\.2/results" in pattern
    wrapped = json.loads(raw[0])
    assert wrapped["complete"] is True and len(wrapped["responses"]) == 2, "a 304's empty body is no answer"


def test_json_mentioning_a_captcha_is_not_a_block_but_html_is():
    assert challenge(Response(200, b'[{"tickets": [], "note": "captcha challenge"}]')) is None
    assert challenge(Response(200, b"<!DOCTYPE HTML><html>blocked</html>"))


def test_parse_recorded_search():
    parsed = Source().parse([FIXTURE.read_bytes()], QUERY, NOW)
    assert len(parsed.offers) == 126 and parsed.notes == []
    sellers = {offer.fare.seller for offer in parsed.offers}
    assert "aviasales:kupibilet" in sellers and "aviasales:aviasales" in sellers and len(sellers) == 8
    offer = parsed.offers[0]
    segment = offer.itinerary.outbound[0]
    assert (segment.flight, segment.origin, segment.destination) == ("JU130", "BEG", "SVO")
    assert segment.departs.isoformat() == "2026-11-14T07:25:00+01:00"
    assert segment.arrives.isoformat() == "2026-11-14T12:40:00+03:00"
    assert offer.itinerary.inbound == () and offer.itinerary.duration().total_seconds() == 3 * 3600 + 15 * 60
    assert offer.fare.price == Money(27637, "RUB") and offer.fare.seller == "aviasales:aviasales"
    assert offer.fare.cabin == "economy" and offer.fare.refundable is False
    assert offer.fare.baggage.checked == 0 and offer.fare.baggage.carry_on is True
    assert offer.fare.link.kind == "results"
    assert offer.fare.link.url == "https://www.aviasales.ru/search/BEG1411MOW1"
    with_bag = next(o for o in parsed.offers if o.fare.price == Money(30305, "RUB"))
    assert with_bag.fare.baggage.checked == 1 and with_bag.fare.baggage.checked_kg == 23


def test_link_only_for_the_observed_url_form():
    business = FlightQuery(("BEG",), ("MOW",), date(2026, 11, 14), cabin="business")
    parsed = Source().parse([FIXTURE.read_bytes()], business, NOW)
    assert parsed.offers[0].fare.link is None and any("no results link" in note for note in parsed.notes)
    round_trip = FlightQuery(("BEG",), ("MOW",), date(2026, 11, 14), return_=date(2026, 11, 20), adults=2)
    assert results_link(round_trip, "BEG", "MOW").url == "https://www.aviasales.ru/search/BEG1411MOW20112"


def test_parse_empty_and_unknown_shape():
    empty = json.dumps({"origin": "BEG", "destination": "MOW", "complete": True, "responses": FINAL}).encode()
    assert Source().parse([empty], QUERY, NOW).offers == []
    with pytest.raises(ParseError):
        Source().parse([b"{}"], QUERY, NOW)


def test_partial_results_are_reported_and_missing_baggage_stays_unknown():
    search = json.loads(FIXTURE.read_bytes())
    search["complete"] = False
    first = search["responses"][0]["tickets"][0]["proposals"][0]["flight_terms"]
    first[next(iter(first))].pop("baggage")
    parsed = Source().parse([json.dumps(search).encode()], QUERY, NOW)
    assert parsed.offers[0].fare.baggage.checked is None
    assert any("partial" in note for note in parsed.notes)


def test_a_ticket_repeated_across_answers_is_one_offer():
    search = json.loads(FIXTURE.read_bytes())
    search["responses"] *= 2
    assert len(Source().parse([json.dumps(search).encode()], QUERY, NOW).offers) == 126


def test_a_ticket_with_a_train_leg_is_left_out_and_counted():
    search = json.loads(FIXTURE.read_bytes())
    payload = search["responses"][0]
    ticket = payload["tickets"][0]
    rail = payload["flight_legs"][ticket["segments"][0]["flights"][0]]
    # As recorded between Moscow and Saint Petersburg: a train, with station codes in place of airports.
    rail.update(origin="ZKD", destination="ZLK", equipment={"code": "", "type": "train", "name": "Сапсан"})
    whole = len(Source().parse([FIXTURE.read_bytes()], QUERY, NOW).offers)
    search["responses"] *= 2
    parsed = Source().parse([json.dumps(search).encode()], QUERY, NOW)
    assert 0 < len(parsed.offers) < whole
    assert all(seg.origin != "ZKD" for offer in parsed.offers for seg in offer.itinerary.outbound)
    assert [note for note in parsed.notes if "train" in note] == [
        "tickets with a train leg left out: 1; this is a flight search"
    ]


@pytest.mark.live
async def test_live_search():
    from tests.live import check_source

    offers = await check_source(Source(), QUERY)
    assert all(offer.fare.seen_at.tzinfo for offer in offers)


def test_a_ticket_into_another_airport_of_the_city_links_to_its_own_page():
    payload = json.loads(FIXTURE.read_bytes())
    payload["destination"] = "VKO"  # as if the search had asked for Vnukovo and the answer brought Sheremetyevo
    parsed = Source().parse([json.dumps(payload).encode()], FlightQuery(("BEG",), ("VKO",), date(2026, 11, 14)), NOW)
    to_svo = next(o for o in parsed.offers if o.itinerary.outbound[-1].destination == "SVO")
    assert to_svo.fare.link.url == "https://www.aviasales.ru/search/BEG1411SVO1"
