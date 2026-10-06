"""The one line every MCP source speaks through: what it sends, and what it makes of each kind of answer."""

import asyncio
import json
from datetime import datetime, timezone

import pytest

from travelops.net.client import Response
from travelops.sources._mcp import Refused, Server, envelope
from travelops.sources.base import Context, ParseError, SourceFault

NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)


class Net:
    """Answers the handshake, then whatever the test put in `answer` for a tool call."""

    def __init__(self, answer=None, status=200, opened=200):
        self.calls, self.answer, self.status, self.opened = [], answer, status, opened

    async def request(self, source, method, url, **kw):
        self.calls.append(kw)
        message = kw["json"]
        if message["method"] == "initialize":
            body = b'{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18"}}'
            return Response(self.opened, body, {"Mcp-Session-Id": "s1"})
        if message["method"] == "notifications/initialized":
            return Response(202, b"")
        body = json.dumps({"jsonrpc": "2.0", "id": message["id"], **self.answer}).encode()
        return Response(self.status, b"event: message\ndata: " + body + b"\n\n")


def result(**fields):
    return {"result": fields}


async def call(net, **server):
    return await Server("demo", "https://mcp.example", **server).call(Context(net, None, lambda: NOW), "search", {})


async def test_one_handshake_for_every_run_of_a_search_and_its_headers_on_each_call():
    net, server = (
        Net(result(structuredContent={"offers": []})),
        Server("demo", "https://mcp.example", queue="handshake"),
    )
    ctx = Context(net, None, lambda: NOW)
    await asyncio.gather(*(server.call(ctx, "search", {"route": n}) for n in range(3)))
    methods = [c["json"]["method"] for c in net.calls]
    assert methods == ["initialize", "notifications/initialized", "tools/call", "tools/call", "tools/call"]
    assert [c.get("queue") for c in net.calls[:2]] == ["handshake", "handshake"], "the handshake waits apart"
    calls = net.calls[2:]
    assert {c["headers"]["mcp-session-id"] for c in calls} == {"s1"}
    assert {c["headers"]["mcp-protocol-version"] for c in calls} == {"2025-06-18"}
    assert len({c["json"]["id"] for c in calls}) == 3, "every call of a session has its own id"


async def test_a_server_without_sessions_gets_no_handshake():
    net = Net(result(structuredContent={"itineraries": []}))
    assert await call(net, handshake=False) == {"itineraries": []}
    assert [c["json"]["method"] for c in net.calls] == ["tools/call"]


async def test_text_is_read_as_json_and_text_for_a_model_is_not_passed_on():
    content = [{"type": "text", "text": "Show each result as a card."}, {"type": "text", "text": '{"trips": []}'}]
    assert await call(Net(result(content=content))) == {"trips": []}
    with pytest.raises(ParseError, match="without data"):
        await call(Net(result(content=content[:1])))


async def test_a_refusal_in_words_is_told_apart_from_a_broken_answer():
    refused = result(isError=True, content=[{"type": "text", "text": "Unknown place: Atlantis"}])
    with pytest.raises(Refused, match="demo refused the query: Unknown place: Atlantis"):
        await call(Net(refused))
    with pytest.raises(ParseError, match="RPC error: bad argument") as broken:
        await call(Net({"error": {"message": "bad argument"}}))
    assert not isinstance(broken.value, Refused)


async def test_server_faults_are_the_site_s_and_not_a_parse_failure():
    with pytest.raises(SourceFault, match="HTTP 502"):
        await call(Net(result(), opened=502))
    with pytest.raises(SourceFault, match="HTTP 503"):
        await call(Net(result(), status=503))
    with pytest.raises(ParseError, match="HTTP 404"):
        await call(Net(result(), status=404))


async def test_a_failed_handshake_is_not_kept():
    net = Net(result(structuredContent={}), opened=502)
    server, ctx = Server("demo", "https://mcp.example"), Context(net, None, lambda: NOW)
    with pytest.raises(SourceFault):
        await server.call(ctx, "search", {})
    net.opened = 200
    assert await server.call(ctx, "search", {}) == {}


def test_the_envelope_is_json_or_the_last_frame_of_a_stream():
    assert envelope(b'event: message\ndata: {"result":{"offers":[]}}\n\n')["result"] == {"offers": []}
    assert envelope(b'{"result":{}}') == {"result": {}}
    with pytest.raises(ParseError):
        envelope(b"<html>busy</html>")
