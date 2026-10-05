import json
import sys
import pytest
from mcp import Client, StdioServerParameters
from travelops import mcp as api
from travelops.app import build
from travelops.core.money import Rates
from travelops.search import FlightSearch, StaySearch


@pytest.fixture
async def app(tmp_path):
    (tmp_path / "profile.yml").write_text(
        "travellers: {adults: 2}\ncabin: business\ncurrency: USD\nstays: {adults: 3}\n"
    )
    value = build(tmp_path, None)
    yield value
    await value.close()


def stub_search(monkeypatch):
    async def rates(net):
        return Rates("EUR", {"USD": 1.2}, "offline-test")

    async def flights(query, sources, ctx, rates, currency, **kwargs):
        return FlightSearch(query, currency, [], [])

    async def stays(query, sources, ctx, rates, currency, **kwargs):
        return StaySearch(query, currency, [], [])

    monkeypatch.setattr(api, "load_rates", rates)
    monkeypatch.setattr(api, "search_flights", flights)
    monkeypatch.setattr(api, "search_stays", stays)
    monkeypatch.setattr(api, "estimate_flights", lambda *args: 0)
    monkeypatch.setattr(api, "estimate_stays", lambda *args: 0)


async def test_confirmation_gate_makes_no_requests(app, monkeypatch):
    monkeypatch.setattr(api, "estimate_flights", lambda *args: 400)

    async def unexpected(*args, **kwargs):
        pytest.fail("unconfirmed search must not run")

    monkeypatch.setattr(api, "load_rates", unexpected)
    data = await api.Tools(app).search_flights("BEG", "MOW", "2026-11-14")
    assert data["needs_confirmation"] is True and data["estimate_seconds"] == 400
    assert not app.net.counts


async def test_direct_defaults_and_json(app, monkeypatch):
    stub_search(monkeypatch)
    tools = api.Tools(app)
    flights = await tools.search_flights("BEG", "MOW", "2026-11-14", sources=["onetwotrip"])
    assert flights["query"]["adults"] == 2 and flights["query"]["cabin"] == "business" and flights["currency"] == "USD"
    stays = await tools.search_stays("Belgrade", "2026-11-14", "2026-11-16")
    assert stays["query"]["adults"] == 3 and stays["cards"] == []
    json.dumps(flights)
    json.dumps(stays)


async def test_results_are_a_shortlist_and_limit_is_validated(app, monkeypatch):
    stub_search(monkeypatch)
    from mcp.server.mcpserver.exceptions import ToolError

    tools = api.Tools(app)
    flights = await tools.search_flights("BEG", "MOW", "2026-11-14", limit=3)
    assert flights["shown"] == {"cards": 0, "of": 0, "offers_per_group_at_most": 5}
    with pytest.raises(ToolError, match="limit"):
        await tools.search_flights("BEG", "MOW", "2026-11-14", limit=0)
    with pytest.raises(ToolError, match="limit"):
        await tools.search_stays("Belgrade", "2026-11-14", "2026-11-16", limit=1000)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_protocol_json_schemas_errors_and_cleanup(app, monkeypatch, mode):
    stub_search(monkeypatch)
    server = api.create_server(app.root, app=app)
    async with Client(server, mode=mode) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools.tools} == {
            "search_flights",
            "search_stays",
            "search_trip",
            "airports_near",
            "sources",
        }
        search = next(t for t in tools.tools if t.name == "search_flights")
        assert search.annotations.read_only_hint is True
        assert search.input_schema["properties"]["depart"]["type"] == "string"
        result = await client.call_tool(
            "search_flights", {"origin": "BEG", "destination": "MOW", "depart": "2026-11-14"}
        )
        assert not result.is_error and result.structured_content["query"]["adults"] == 2
        invalid = await client.call_tool("search_flights", {"origin": "BEG", "destination": "MOW", "depart": "bad"})
        assert invalid.is_error and "Invalid" in invalid.content[0].text
    assert app.closed


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_actual_stdio_entrypoint_sources_only(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("TRAVELOPS_DATA", str(tmp_path / "data"))
    params = StdioServerParameters(
        command=sys.executable, args=["-c", 'from travelops.cli import main; main(["mcp"])'], cwd=str(tmp_path)
    )
    async with Client(params, mode=mode, read_timeout_seconds=10) as client:
        result = await client.call_tool("sources", {})
        assert not result.is_error
        assert "airbnb" in result.structured_content["stay_sources"]
        assert "tutu" in result.structured_content["flight_sources"]
