from types import SimpleNamespace

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from travelops.mcp import Tools
from travelops.profile import Profile
from travelops.watch import Watches, check, judge, run_due

DAY = 86400
NOW = 1_790_000_000.0  # 2026-09-21


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def flights(price, link="https://example.test/offer"):
    fare = {"converted": {"amount": str(price), "currency": "EUR"}, "seller": "kiwi", "link": {"url": link}}
    return {
        "search_id": f"f{int(price):07d}",
        "cards": [{"groups": [{"fares": [fare]}]}],
        "report": {"ok": ["kiwi"], "empty": [], "problems": [], "limits": []},
    }


class FakeTools:
    def __init__(self, prices):
        self.prices, self.calls = list(prices), []

    async def search_flights(self, **kw):
        self.calls.append(("search", kw))
        return flights(self.prices.pop(0))

    async def refine_flights(self, search_id, **kw):
        self.calls.append(("refine", kw))
        return flights(int(search_id[1:]))


ARGS = {"origin": "BEG", "destination": "LIS", "depart": "2026-11-14", "return_date": "2026-11-16"}


def test_judge():
    assert judge(None, 100, None, 5) is None  # the first check only sets the reference
    assert judge(100, 96, None, 5) is None
    assert judge(100, 95, None, 5) == "5% below the 100 last told"
    assert judge(None, 80, 90, 5) == "at or under 90"
    assert judge(85, 84, 90, 5) is None  # already under the bar and told so
    assert judge(100, None, 90, 5) is None


async def test_a_slow_slide_adds_up_to_an_alert():
    clock = Clock()
    watches = Watches(None, clock)
    watch = watches.add("flights", ARGS, {"max_stops": 0}, "EUR", every_hours=6)
    tools = FakeTools([300, 290, 282, 284])
    said = []
    for _ in range(4):
        await run_due(tools, watches, said.append)
        clock.now += 6 * 3600
    assert [c[0] for c in tools.calls[:2]] == ["search", "refine"]
    assert tools.calls[0][1]["refresh"] is True and tools.calls[0][1]["currency"] == "EUR"
    assert tools.calls[1][1] == {"limit": 1, "max_stops": 0}
    # 300 sets the reference; 290 is 3% down; 282 is 6% below 300 and alerts; 284 is above the new 282.
    alerts = [line for line in said if "—" in line]
    assert len(alerts) == 1 and "282 EUR" in alerts[0]
    (alert,) = watches.pending()
    assert alert["price"] == 282 and alert["last_told"] == 300 and alert["link"] == "https://example.test/offer"
    assert alert["watch_id"] == watch.id and alert["why"] == "6% below the 300 last told"
    assert watches.pending() == []  # collected once: the assistant that took it tells the human
    assert watches.get(watch.id).told == 282
    assert [c["best"] for c in watches.history(watch.id)] == [284, 282, 290, 300]


async def test_not_due_until_its_hours_pass_and_stops_on_departure_day():
    clock = Clock()
    watches = Watches(None, clock)
    watch = watches.add("flights", ARGS, {}, "EUR", every_hours=6)
    tools = FakeTools([100, 100])
    await run_due(tools, watches, lambda _: None)
    await run_due(tools, watches, lambda _: None)
    assert len(tools.calls) == 1
    clock.now = NOW + 60 * DAY  # past 2026-11-14
    assert watches.due() == [] and not watches.get(watch.id).active


async def test_a_failed_search_is_recorded_and_others_go_on():
    class Broken(FakeTools):
        async def search_flights(self, **kw):
            raise ValueError("source down")

    watches = Watches(None, Clock())
    watch = watches.add("flights", ARGS, {}, "EUR")
    why, line = await check(Broken([]), watches, watch)
    assert why is None and "source down" in line
    assert watches.history(watch.id)[0]["note"] == "failed: source down"


def test_limits_on_adding():
    watches = Watches(None, Clock())
    with pytest.raises(ValueError, match="at least 3"):
        watches.add("flights", ARGS, {}, "EUR", every_hours=1)
    with pytest.raises(ValueError, match="kind"):
        watches.add("trains", ARGS, {}, "EUR")


async def test_tool_checks_arguments_without_searching():
    tools = Tools(SimpleNamespace(profile=Profile(), watches=Watches(None, Clock())))
    saved = await tools.watch_price("flights", ARGS, {"max_stops": 0, "checked_bag": True}, below=250)
    assert saved["what"] == "BEG→LIS 2026-11-14/2026-11-16" and saved["below"] == 250
    with pytest.raises(ToolError, match="refresh"):
        await tools.watch_price("flights", dict(ARGS, refresh=True))
    with pytest.raises(ToolError, match="unexpected keyword"):
        await tools.watch_price("flights", ARGS, {"cheapest": True})
    with pytest.raises(ToolError, match="isoformat"):
        await tools.watch_price("flights", dict(ARGS, depart="14.11"))
    assert len((await tools.watches())["watches"]) == 1
    stopped = await tools.stop_watch(saved["watch_id"])
    assert stopped["active"] is False and (await tools.watches())["watches"] == []


def test_a_ground_watch_lasts_until_its_day():
    watches = Watches(None, Clock())
    watch = watches.add("ground", {"origin": "Belgrade", "destination": "Vienna", "depart": "2026-11-14"}, {}, "EUR")
    assert watch.until == "2026-11-14" and watch.label() == "Belgrade→Vienna 2026-11-14 by train/bus"


async def test_an_alert_gives_the_seller_and_link_of_the_price_it_tells():
    # The first fare of the card has no rate into EUR: it is not the price the watch tells, so not its link either.
    foreign = {"price": {"amount": "90", "currency": "XXX"}, "converted": None, "seller": "far", "link": {"url": "x"}}
    priced = {"converted": {"amount": "120", "currency": "EUR"}, "seller": "kiwi", "link": {"url": "https://k.test"}}

    class Mixed(FakeTools):
        async def search_flights(self, **kw):
            result = flights(0)
            result["cards"] = [{"groups": [{"fares": [foreign]}, {"fares": [priced]}]}]
            return result

    watches = Watches(None, Clock())
    watch = watches.add("flights", ARGS, {}, "EUR", below=150)
    alert, _ = await check(Mixed([]), watches, watch)
    assert (alert["price"], alert["seller"], alert["link"]) == (120, "kiwi", "https://k.test")
