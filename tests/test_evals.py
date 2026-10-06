import json
from pathlib import Path

import pytest

from travelops import mcp as api
from travelops.evals import amounts_in, check_run, check_turn, collect, number
from travelops.replay import build_replay

SCENARIO = Path(__file__).parents[1] / "evals/scenarios/kotor-weekend"


def test_money_is_read_the_way_people_write_it():
    assert [str(number(x)) for x in ("10 701", "10,701", "113.60", "113,60", "1.250")] == [
        "10701",
        "10701",
        "113.60",
        "113.60",
        "1250",
    ]
    found = amounts_in("в 21:25. 10 701 RUB") + amounts_in("12 22.10. 9 EUR")
    assert [(str(a), c) for a, c, _ in found] == [("10701", "RUB"), ("9", "EUR")]
    found = amounts_in("от 113,60 EUR (10 701 ₽), жильё €29, итого 142.60 евро; 5 км и 2 пересадки")
    assert [(str(a), c) for a, c, _ in found] == [("113.60", "EUR"), ("10701", "RUB"), ("29", "EUR"), ("142.60", "EUR")]


def turn(**answer):
    return {"say": "x", "answer": answer}


def test_an_invented_price_or_link_fails_and_arithmetic_does_not():
    amounts, links = set(), set()
    collect(
        {
            "cards": [
                {
                    "price": {"amount": "113.60", "currency": "EUR"},
                    "link": {"url": "https://a.example/t", "kind": "ticket"},
                }
            ],
            "stay": {"total": {"amount": "29", "currency": "EUR"}, "photos": ["https://img.example/1.webp"]},
        },
        amounts,
        links,
    )
    good = "Перелёт 113.60 EUR https://a.example/t, жильё 29 EUR, вместе 142.60 EUR. Фото: https://img.example/1.webp."
    assert check_turn(turn(language="ru"), good, [], amounts, links) == []
    bad = "Перелёт около 110 EUR, вот ссылка https://a.example/other и таблица:\n| a | b |\n|---|---|\n"
    failures = check_turn(turn(language="ru", links_at_least=2), bad, [], amounts, links)
    assert any("price '110 EUR'" in f for f in failures)
    assert any("link is in no tool result" in f for f in failures)
    assert any("table" in f for f in failures) and any("at least 2" in f for f in failures)


def test_calls_are_checked_against_what_the_turn_needs():
    rules = {
        "say": "x",
        "calls": {
            "require": [{"tool": "refine_flights", "arguments": {"search_id": "F1", "airlines": ["ju"]}}],
            "forbid": ["search_trip"],
            "searches_at_most": 0,
        },
    }
    fine = [{"tool": "refine_flights", "arguments": {"search_id": "f1", "airlines": ["JU"], "limit": 3}, "result": {}}]
    assert check_turn(rules, "", fine, set(), set()) == []
    wrong = [
        {"tool": "search_trip", "arguments": {}, "result": {}},
        {"tool": "search_flights", "arguments": {}, "error": "boom"},
    ]
    failures = check_turn(rules, "", wrong, set(), set())
    assert len(failures) == 4 and any("no call like" in f for f in failures) and any("boom" in f for f in failures)


def test_unreported_sources_and_missing_facts_fail():
    calls = [
        {"tool": "search_stays", "arguments": {}, "result": {"sources": [{"source": "booking"}, {"source": "airbnb"}]}}
    ]
    rules = turn(mention_all_sources=True, must_match=["Тиват|TIV"], must_not_match=["гарантир"])
    failures = check_turn(rules, "Booking: ok. Гарантирую лучшую цену.", calls, set(), set())
    assert failures == [
        "source airbnb is not reported",
        "nothing in the answer matches /Тиват|TIV/",
        "the answer has 'Гарантир', matching /гарантир/",
    ]


async def test_a_recorded_scenario_replays_without_the_network(tmp_path, monkeypatch):
    app = build_replay(SCENARIO)
    try:
        tools = api.Tools(app)
        trip = await tools.search_trip("BEG", "Kotor", "2026-10-22", "2026-10-23", country="ME")
        assert trip["flights"]["search_id"] == "fwgug2dt" and trip["flights"]["from_memory"] is False
        assert trip["flights"]["age_minutes"] <= 3 and len(trip["flights"]["cards"]) == 5
        assert {r["source"] for r in trip["stays"]["sources"]} == {"booking", "airbnb", "trivago"}
        assert "kiwi" in trip["flights"]["report"]["ok"]
        evening = await tools.refine_flights("fwgug2dt", return_after="17:00")
        assert evening["cards"] and all(c["inbound"][0]["departs"][11:16] >= "17:00" for c in evening["cards"])
        two = await tools.search_trip("BEG", "Kotor", "2026-10-22", "2026-10-23", country="ME", separate_tickets=True)
        assert two["separate_tickets"]["pairs"][0]["total"] == {"amount": "105.64", "currency": "EUR"}
        pages = await tools.stay_details("sj4bt6gc", ["Ivi 5", "Midpoint"])
        assert [s["status"] for s in pages["stays"]] == ["ok", "not_configured"] and "Kitchen" in pages["stays"][0][
            "amenities"
        ]
        equipped = await tools.refine_stays("sj4bt6gc", must_have=["wifi", "kitchen"], exclude_kinds=["shared_room"])
        assert {c["stay"]["name"] for c in equipped["cards"]} == {"Ivi 5", "Majka 2 Studio 2"}
        from mcp.server.mcpserver.exceptions import ToolError

        with pytest.raises(ToolError, match="replay has no flights search"):
            await tools.search_flights("BEG", "IST", "2026-10-22")
        with pytest.raises(ToolError, match="replay holds no answer"):
            await tools.airports_near("Budva")
        assert not app.net.counts, "a replay sends nothing"
    finally:
        await app.close()


async def test_a_later_turn_meets_old_prices(monkeypatch):
    monkeypatch.setenv("TRAVELOPS_REPLAY_MINUTES", "50")
    app = build_replay(SCENARIO)
    try:
        tools = api.Tools(app)
        view = await tools.refine_flights("fwgug2dt")
        assert view["age_minutes"] >= 50 and "search again" in view["stale"]
        again = await tools.search_trip("BEG", "Kotor", "2026-10-22", "2026-10-23", country="ME")
        assert again["flights"]["age_minutes"] == 0 and "stale" not in again["flights"], "asked again, searched again"
    finally:
        await app.close()


def test_a_run_is_checked_turn_by_turn(tmp_path):
    (tmp_path / "turn-1.md").write_text("Ничего не нашёл.")
    (tmp_path / "turn-1.trace.jsonl").write_text(
        json.dumps({"tool": "search_trip", "arguments": {"origin": "BEG", "place": "Budva"}, "result": {}}) + "\n"
    )
    report = check_run(SCENARIO, tmp_path)
    assert [t["turn"] for t in report] == [1, 2, 3, 4, 5, 6]
    assert any("no call like" in f for f in report[0]["failures"])
    assert report[1]["failures"] == ["no answer: turn-2.md is missing"]


async def test_the_family_scenario_replays_with_its_party_and_its_refusals():
    app = build_replay(Path(__file__).parents[1] / "evals/scenarios/lisbon-family")
    try:
        tools = api.Tools(app)
        trip = await tools.search_trip(
            "BEG", "Lisbon", "2026-11-12", "2026-11-16", country="Portugal", adults=2, children_ages=[7]
        )
        assert trip["flights"]["search_id"] == "fzvo46bk" and trip["stays"]["search_id"] == "smnz3xsu"
        refused = {p["source"] for p in trip["flights"]["report"]["problems"]}
        assert refused == {"onetwotrip", "kupibilet", "wildberries"}
        assert {a["iata"]: a.get("minutes_road") for a in trip["airports_in_reach"]}["LIS"] == 14, (
            "the drive is recorded"
        )
        bags = await tools.refine_flights("fzvo46bk", checked_bag=True, max_connection_hours=3)
        assert bags["cards"][0]["groups"][0]["fares"][0]["converted"]["amount"] == "1163.33"
    finally:
        await app.close()


def test_links_with_brackets_are_read_whole():
    from travelops.evals import URL, clean_url

    url = "https://avia.tutu.ru/f/A/B/?travelers=3&route[0]=144-1&route[1]=303-1"
    written = f"см. [Tutu]({url}). И ещё ({url})."
    assert [clean_url(u) for u in URL.findall(written)] == [url, url]
