import json
import sys
import pytest
from mcp import Client, StdioServerParameters
from dataclasses import replace
from travelops import mcp as api
from travelops.app import build
from tests.fakes import fake_searches


@pytest.fixture
async def app(tmp_path):
    (tmp_path / "profile.yml").write_text(
        "travellers: {adults: 2}\ncabin: business\ncurrency: USD\nstays: {adults: 3}\n"
    )
    value = build(tmp_path, None)
    yield value
    await value.close()


def fake_tools(app, **kwargs):
    return api.Tools(app, fake_searches(app, **kwargs))


async def unexpected(*args, **kwargs):
    pytest.fail("this must not run")


async def test_confirmation_gate_makes_no_requests(app):
    tools = fake_tools(app, estimate=lambda *args: 400, flights=unexpected, load=unexpected)
    data = await tools.search_flights(origin="BEG", destination="MOW", depart="2026-11-14")
    assert data["needs_confirmation"] is True and data["estimate_seconds"] == 400
    assert not app.net.counts


async def test_direct_defaults_and_json(app):
    tools = fake_tools(app)
    flights = await tools.search_flights(origin="BEG", destination="MOW", depart="2026-11-14", sources=["onetwotrip"])
    assert flights["query"]["adults"] == 2 and flights["query"]["cabin"] == "business" and flights["currency"] == "USD"
    stays = await tools.search_stays("Belgrade", "2026-11-14", "2026-11-16")
    assert stays["query"]["adults"] == 3 and stays["cards"] == []
    json.dumps(flights)
    json.dumps(stays)


async def test_results_are_a_shortlist_and_limit_is_validated(app):
    from mcp.server.mcpserver.exceptions import ToolError

    tools = fake_tools(app)
    flights = await tools.search_flights(origin="BEG", destination="MOW", depart="2026-11-14", limit=3)
    assert flights["shown"] == {
        "cards": 0,
        "of": 0,
        "offers_per_group_at_most": 5,
        "of_counts": "cards left after `filtered`",
        "sorted_by": "price",
    }
    with pytest.raises(ToolError, match="limit"):
        await tools.search_flights(origin="BEG", destination="MOW", depart="2026-11-14", limit=0)
    with pytest.raises(ToolError, match="limit"):
        await tools.search_stays("Belgrade", "2026-11-14", "2026-11-16", limit=1000)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_protocol_json_schemas_errors_and_cleanup(app, mode):
    server = api.create_server(app.root, app=app, searches=fake_searches(app))
    async with Client(server, mode=mode) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools.tools} == {
            "search_flights",
            "search_stays",
            "search_ground",
            "refine_ground",
            "search_trip",
            "refine_flights",
            "refine_stays",
            "stay_details",
            "stay_photos",
            "airports_near",
            "sources",
            "watch_price",
            "watches",
            "stop_watch",
            "watch_alerts",
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


async def test_a_follow_up_is_a_view_of_the_search_and_costs_nothing(app):
    from mcp.server.mcpserver.exceptions import ToolError

    tools = fake_tools(app)
    first = await tools.search_flights(origin="BEG", destination="MOW", depart="2026-11-14")
    assert first["from_memory"] is False

    kinds = tools.searches.kinds
    kinds["flights"] = replace(kinds["flights"], search=unexpected)  # a follow-up must not search
    again = await tools.search_flights(origin="BEG", destination="MOW", depart="2026-11-14", limit=50)
    assert again["from_memory"] is True and again["search_id"] == first["search_id"]
    view = await tools.refine_flights(first["search_id"], depart_after="17:00", sort="duration")
    assert view["search_id"] == first["search_id"] and view["shown"]["sorted_by"] == "duration"
    assert view["filtered"]["by"] == [
        {"filter": "cabin", "value": "business", "hidden": 0},
        {"filter": "max_stops", "value": 1, "hidden": 0},
        {"filter": "max_leg_hours", "value": 24, "hidden": 0},
        {"filter": "depart_after", "value": "17:00", "hidden": 0},
    ], "a view starts from the bars of the profile, as the search did"
    assert view["report"] == {"ok": [], "empty": [], "problems": [], "limits": []}
    assert not app.net.counts
    with pytest.raises(ToolError, match="no search"):
        await tools.refine_flights("f0000000")
    with pytest.raises(ToolError, match="flights search"):
        await tools.refine_stays(first["search_id"])
    with pytest.raises(ToolError, match="HH:MM"):
        await tools.refine_flights(first["search_id"], depart_after="5pm")


async def test_an_old_search_is_shown_with_its_age_and_not_reused(app):
    tools = fake_tools(app)
    now = [1_000_000.0]
    app.results.clock = lambda: now[0]
    first = await tools.search_stays("Belgrade", "2026-11-14", "2026-11-16")
    now[0] += 45 * 60
    view = await tools.refine_stays(first["search_id"])
    assert view["age_minutes"] == 45 and "search again" in view["stale"]
    later = await tools.search_stays("Belgrade", "2026-11-14", "2026-11-16")
    assert later["from_memory"] is False and later["search_id"] != first["search_id"]


def stay_result():
    def card(name, source, source_id, total, **stay):
        return {
            "stay": dict(
                {"source": source, "source_id": source_id, "name": name, "kind": "other", "lat": None, "lon": None},
                rating=9.0,
                reviews=50,
                photos=["https://img.example/" + source_id + ".jpg"],
                amenities=[],
                district=None,
                center_km=None,
                **stay,
            ),
            "nights": 1,
            "rates": [
                {
                    "total": {"amount": str(total), "currency": "EUR"},
                    "converted": {"amount": str(total), "currency": "EUR"},
                    "link": {"url": f"https://{source}.example/{source_id}", "kind": "property"},
                    "seen_at": "2026-10-05T23:40:00+00:00",
                    "room": None,
                }
            ],
        }

    return {
        "query": {"place": "Kotor, Montenegro"},
        "currency": "EUR",
        "center": {"name": "Kotor, Montenegro", "lat": 42.4207, "lon": 18.7683},
        "cards": [
            card("Guest House One", "booking", "/hotel/me/one.html", 24),
            card("Old Town Flat", "airbnb", "111", 30, **{}),
            card("Hill View", "airbnb", "222", 35),
        ],
        "sources": [],
    }


async def test_details_are_read_once_kept_and_open_the_must_have_filter(app, monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError
    from travelops import details as service
    from travelops.sources.base import ParseError

    asked = []

    class Page:
        def __init__(self, source):
            self.source = source

        async def fetch_details(self, url, place, ctx):
            asked.append((self.source, url, place))
            return b"page"

        def parse_details(self, raw):
            if self.source == "airbnb" and len(asked) == 3:
                raise ParseError("Airbnb listing state missing")
            wifi = ["Free WiFi", "Kitchenette"] if self.source == "booking" else ["Wifi"]
            return {
                "kind": "shared_room" if self.source == "airbnb" else None,
                "lat": 42.4253,
                "lon": 18.7703,
                "address": "Old Town",
                "amenities": wifi,
                "not_available": [],
                "photos": ["https://img.example/more.jpg"],
                "check_in": None,
                "check_out": None,
                "rules": [],
                "scores": {},
            }

    monkeypatch.setattr(service, "STAY_SOURCES", {"booking": lambda: Page("booking"), "airbnb": lambda: Page("airbnb")})
    tools = api.Tools(app)
    stored = app.results.put("stays", "k", stay_result())
    before = await tools.refine_stays(stored.id, must_have=["wifi"])
    assert before["cards"] == [] and before["filtered"]["by"][-1] == {
        "filter": "must_have",
        "value": ["wifi"],
        "hidden": 0,
        "hidden_unknown": 3,
    }, "a search card carries no amenities: unknown, not failing"

    read = await tools.stay_details(stored.id, ["guest house", "111", "Hill View", "Nowhere Inn"])
    assert [s["status"] for s in read["stays"]] == ["ok", "ok", "unparsed", "not_found"]
    assert asked[0] == ("booking", "https://booking.example//hotel/me/one.html", "Kotor, Montenegro")
    one = read["stays"][0]
    assert one["amenities"] == ["Free WiFi", "Kitchenette"] and one["photos_total"] == 2 and one["from_memory"] is False
    assert read["stays"][1]["kind"] == "shared_room", "the page says what the card did not"

    again = await tools.stay_details(stored.id, ["Guest House One"])
    assert again["stays"][0]["from_memory"] is True and len(asked) == 3, "a page already read is not read again"

    after = await tools.refine_stays(stored.id, must_have=["wifi", "kitchen"])
    assert [c["stay"]["name"] for c in after["cards"]] == ["Guest House One"]
    assert after["filtered"]["by"][-1] == {
        "filter": "must_have",
        "value": ["kitchen", "wifi"],
        "hidden": 1,
        "hidden_unknown": 1,
    }
    seen = (await tools.refine_stays(stored.id, exclude_kinds=["shared_room"], sort="center"))["cards"]
    assert [c["stay"]["name"] for c in seen] == ["Guest House One", "Hill View"], (
        "the flat turned out to be a dormitory"
    )
    assert seen[0]["stay"]["center_km"] == 0.54 and "straight line" in seen[0]["stay"]["center_km_measured"]
    with pytest.raises(ToolError, match="from 1 to 5"):
        await tools.stay_details(stored.id, [])


async def test_photos_come_back_as_images_with_their_links(app, monkeypatch):
    from travelops.net.client import Response

    async def request(source, method, url, **kw):
        assert source == "images" and "avif" not in kw["headers"]["accept"]
        return Response(200, b"\xff\xd8\xff\xe0" + b"0" * 64) if "one" in url else Response(404, b"gone")

    monkeypatch.setattr(app.net, "request", request)
    stored = app.results.put("stays", "k", stay_result())
    server = api.create_server(app.root, app=app)
    async with Client(server) as client:
        result = await client.call_tool(
            "stay_photos", {"search_id": stored.id, "stays": ["Guest House One", "Hill View"]}
        )
        kinds = [c.type for c in result.content]
        assert kinds == ["text", "text", "image", "text", "text"]
        assert result.content[1].text == "photo 1: https://img.example//hotel/me/one.html.jpg"
        assert result.content[2].mime_type == "image/jpeg"
        assert "could not be loaded (HTTP 404)" in result.content[4].text


async def test_over_http_one_post_is_one_call(app):
    """What a router in the next container sends: a JSON-RPC `tools/call` in one POST, no session, JSON back."""
    import httpx

    web = api.http_app(api.create_server(app.root, app=app, searches=fake_searches(app)), "0.0.0.0")
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}

    def call(ident, name, arguments):
        return {"jsonrpc": "2.0", "id": ident, "method": "tools/call", "params": {"name": name, "arguments": arguments}}

    flights = {"origin": "BEG", "destination": "MOW", "depart": "2026-11-14"}
    async with web.router.lifespan_context(web):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url="http://travel-ops:8765") as http:
            found = (await http.post(api.HTTP_PATH, json=call(7, "search_flights", flights), headers=headers)).json()
            refused = (
                await http.post(api.HTTP_PATH, json=call(8, "search_flights", dict(flights, limit=0)), headers=headers)
            ).json()
            alerts = (await http.post(api.HTTP_PATH, json=call(9, "watch_alerts", {}), headers=headers)).json()
    assert found["id"] == 7 and found["result"]["isError"] is False
    assert found["result"]["structuredContent"]["query"]["origins"] == ["BEG"], "the result as data, not only text"
    assert refused["result"]["isError"] is True and "limit must be" in refused["result"]["content"][0]["text"]
    assert alerts["result"]["structuredContent"]["alerts"] == [], "the app built once serves every call"
    assert app.closed, "and is closed with the server"
