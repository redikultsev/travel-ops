import json
from datetime import date, datetime, timezone
from pathlib import Path

from travelops.core.flights import FlightQuery
from travelops.net.client import Response
from travelops.sources.base import Context
from travelops.sources.flights import google
from travelops.sources.flights.google import Source

FIXTURES = Path(__file__).parents[1] / "fixtures" / "google"
NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)
ONE_WAY = FlightQuery(("BEG",), ("IST",), date(2026, 11, 14))
ROUND = FlightQuery(("BEG",), ("IST",), date(2026, 11, 14), date(2026, 11, 17))


def raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_a_one_way_page_is_read_into_priced_flights_with_a_link_to_google():
    parsed = Source().parse([raw("beg-ist-2026-11-14.json")], ONE_WAY, NOW)
    assert len(parsed.offers) == 8
    cheapest = min(parsed.offers, key=lambda o: o.fare.price.amount)
    assert (str(cheapest.fare.price.amount), cheapest.fare.price.currency) == ("79", "EUR")
    assert cheapest.itinerary.outbound[0].flight.startswith("JU") and cheapest.itinerary.inbound == ()
    assert cheapest.itinerary.outbound[0].departs.utcoffset().total_seconds() == 3600, "local time at Belgrade"
    assert cheapest.fare.cabin == "economy" and cheapest.fare.seller == "google"
    assert cheapest.fare.link.kind == "results" and cheapest.fare.link.url.startswith(google.PAGE + "?tfs=")
    assert any("metasearch" in note for note in parsed.notes)


def test_a_round_trip_pairs_each_tried_outbound_with_its_returns():
    parsed = Source().parse([raw("beg-ist-2026-11-14-17.json")], ROUND, NOW)
    assert len(parsed.offers) == 15 and all(o.itinerary.inbound for o in parsed.offers)
    cheapest = min(parsed.offers, key=lambda o: o.fare.price.amount)
    assert str(cheapest.fare.price.amount) == "149"
    assert cheapest.itinerary.inbound[0].origin == "IST" and cheapest.itinerary.inbound[-1].destination == "BEG"
    assert any(n.startswith("returns were looked up for the 3 cheapest outbound flights;") for n in parsed.notes)


def test_the_query_is_one_protobuf_in_the_url_and_a_pinned_outbound_changes_it():
    plain = google.tfs(ROUND, "BEG", "IST")
    pinned = google.tfs(
        ROUND, "BEG", "IST", [{"from": "BEG", "to": "IST", "date": "2026-11-14", "carrier": "JU", "number": "426"}]
    )
    assert plain != pinned and "=" not in plain
    party = google.tfs(FlightQuery(("BEG",), ("IST",), date(2026, 11, 14), adults=2, infants=1), "BEG", "IST")
    assert party != google.tfs(ONE_WAY, "BEG", "IST")


async def test_fetch_reads_the_result_block_of_each_page_and_pins_the_cheapest_outbounds(monkeypatch):
    monkeypatch.setattr(google, "OUTBOUNDS_TRIED", 3)  # the recording pinned three
    pages = json.loads(raw("beg-ist-2026-11-14-17.json"))["pages"]

    def html(payload):
        blob = json.dumps(payload)
        return f"<script>AF_initDataCallback({{key: 'ds:1', hash: '1', data:{blob}, sideChannel: {{}}}});</script>"

    class Net:
        calls = []

        async def request(self, source, method, url, **kw):
            self.calls.append((url, kw.get("cookies")))
            page = pages[0] if len(self.calls) == 1 else pages[len(self.calls) - 1]
            return Response(200, html(page["payload"]).encode(), {}, url)

    raws = await Source().fetch(ROUND, Context(Net(), None, lambda: NOW))
    assert len(Net.calls) == 1 + google.OUTBOUNDS_TRIED == Source().max_requests(ROUND)
    assert all(url.startswith(google.PAGE) and cookies == google.CONSENT for url, cookies in Net.calls)
    assert len(Source().parse(raws, ROUND, NOW).offers) == 15


def test_a_page_without_a_result_block_says_so():
    assert google.payload_of("<html>consent</html>") is None
    empty = json.dumps({"route": ["BEG", "IST"], "pages": [{"tfs": "x", "payload": None}]}).encode()
    parsed = Source().parse([empty], ONE_WAY, NOW)
    assert parsed.offers == [] and "carried no results" in parsed.notes[0]
