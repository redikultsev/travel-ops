"""Searching as every kind goes through it, and trains and buses through the tools an agent calls."""

from datetime import datetime, timezone

import pytest

from travelops.app import build
from travelops.core.common import Link
from travelops.core.ground import GroundOffer, Ride
from travelops.core.money import Money
from travelops.core.report import SourceReport, Status
from travelops.evals import SEARCHES
from travelops.kinds import KINDS
from travelops.mcp import Tools
from travelops.search import GroundSearch
from travelops.searches import NeedsConfirmation
from travelops.watch import Watches, check
from tests.fakes import fake_searches

SEEN = datetime(2026, 10, 7, tzinfo=timezone.utc)
ARGUMENTS = {
    "flights": {"origin": "BEG", "destination": "LIS", "depart": "2026-11-14"},
    "stays": {"place": "Lisbon", "checkin": "2026-11-14", "checkout": "2026-11-16"},
    "ground": {"origin": "Belgrade", "destination": "Vienna", "depart": "2026-11-14"},
}


@pytest.fixture
async def app(tmp_path):
    value = build(tmp_path, None)
    yield value
    await value.close()


def ride(mode, departs, arrives, price, currency, source):
    return GroundOffer(
        (Ride(mode, "Belgrade", "Vienna", datetime.fromisoformat(departs), datetime.fromisoformat(arrives)),),
        Money(price, currency),
        source,
        source,
        Link(f"https://{source}.example/{departs}", "results"),
        SEEN,
    )


async def rides(query, sources, ctx, rates, currency, **kwargs):
    offers = [
        ride("bus", "2026-11-14T22:00:00+01:00", "2026-11-15T07:00:00+01:00", 45, "EUR", "12go"),
        ride("train", "2026-11-14T07:00:00+01:00", "2026-11-14T17:00:00+01:00", 60, "EUR", "tutu"),
        ride("bus", "2026-11-14T08:00:00+01:00", "2026-11-14T16:30:00+01:00", 30, "EUR", "12go"),
        # No rate into EUR: cheapest by its own number, but not comparable, so it goes last.
        ride("bus", "2026-11-14T09:00:00+01:00", "2026-11-14T18:00:00+01:00", 5, "XXX", "tutu"),
    ]
    reports = [SourceReport("tutu", Status.OK, "", [], 2, 2, 1.0), SourceReport("12go", Status.OK, "", [], 2, 1, 1.0)]
    return GroundSearch(query, currency, offers, reports)


@pytest.mark.parametrize("kind", list(KINDS))
async def test_every_kind_is_searched_once_then_viewed_from_memory(app, kind):
    searches = fake_searches(app)
    query = KINDS[kind].read(app.profile, ARGUMENTS[kind])
    (first,) = await searches.run(kind, [query], sources=list(KINDS[kind].registry)[:1], currency="eur")
    assert first.fresh and first.kind == kind and first.id.startswith(kind[0])
    assert all("source selection" in r["notes"][-1] for r in first.result["sources"])
    assert searches.estimate(kind, [query], list(KINDS[kind].registry)[:1], "EUR") == 0, "memory answers"
    (again,) = await searches.run(kind, [query], sources=list(KINDS[kind].registry)[:1], currency="EUR")
    assert again.id == first.id and not again.fresh
    view = searches.refine(kind, first.id, 5)
    assert view["search_id"] == first.id and view["from_memory"] is True and view["shown"]["sorted_by"] == "price"
    applied = {f["filter"] for f in view.get("filtered", {}).get("by", [])}
    assert {bar for bar, value in KINDS[kind].bars(app.profile).items() if value} <= applied, "the profile's bars"
    other = next(name for name in KINDS if name != kind)
    with pytest.raises(ValueError, match=f"is a {kind} search"):
        searches.refine(other, first.id)


@pytest.mark.parametrize("kind", list(KINDS))
async def test_a_long_wait_asks_first_and_sends_nothing(app, kind):
    async def unexpected(*args, **kwargs):
        pytest.fail("an unconfirmed search must not load rates or search")

    searches = fake_searches(app, estimate=lambda *args: 400.0, load=unexpected, **{kind: unexpected})
    query = KINDS[kind].read(app.profile, ARGUMENTS[kind])
    with pytest.raises(NeedsConfirmation) as asked:
        await searches.run(kind, [query])
    assert asked.value.json()["estimate_seconds"] == 400.0


def test_every_kind_is_a_search_the_checks_count():
    assert set(SEARCHES) == {"search_trip", "search_flights", "search_stays", "search_ground"}


async def test_trains_and_buses_through_the_tools(app):
    tools = Tools(app, fake_searches(app, ground=rides))
    found = await tools.search_ground("Belgrade", "Vienna", "2026-11-14", limit=2)
    assert found["search_id"].startswith("g") and found["shown"] == {
        "cards": 2,
        "rest": 2,
        "of": 4,
        "offers_per_group_at_most": 5,
        "sorted_by": "price",
    }
    assert [c["fares"][0]["price"]["amount"] for c in found["cards"]] == ["30", "45"]
    everything = await tools.refine_ground(found["search_id"], limit=10)
    assert [c["fares"][0]["price"]["amount"] for c in everything["cards"]] == ["30", "45", "60", "5"]
    assert found["report"]["ok"] == ["tutu", "12go"]
    view = await tools.refine_ground(found["search_id"], modes=["bus"], depart_after="07:30", max_price=40)
    assert [c["rides"][0]["departs"][11:16] for c in view["cards"]] == ["08:00"]
    assert view["filtered"]["by"][-1] == {"filter": "max_price", "value": 40, "hidden": 1, "hidden_unknown": 1}
    later = await tools.refine_ground(found["search_id"], sort="departure")
    assert [c["rides"][0]["departs"][11:16] for c in later["cards"]][:2] == ["07:00", "08:00"]


async def test_a_ground_watch_alerts_with_the_offer_it_priced(app):
    tools = Tools(app, fake_searches(app, ground=rides))
    watches = Watches(None, lambda: SEEN.timestamp())
    watch = watches.add("ground", ARGUMENTS["ground"], {"modes": ["bus"]}, "EUR", below=40)
    alert, line = await check(tools, watches, watch)
    assert alert["price"] == 30 and alert["link"] == "https://12go.example/2026-11-14T08:00:00+01:00"
    assert "Belgrade→Vienna 2026-11-14 by train/bus: 30 EUR" in line
