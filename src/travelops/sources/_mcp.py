"""A public MCP server spoken to over plain HTTP: the session handshake and one tool call. Only search tools are
ever named by a source; whatever else a server offers is never called."""

from __future__ import annotations

import json

from .base import Context, ParseError, SourceFault
from .flights.kiwi import envelope


class Refused(ParseError):
    """The tool answered, with a refusal in words: an unknown place, a date out of range."""


HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream"}


async def open_session(ctx: Context, source: str, url: str, queue: str | None = None) -> dict:
    """The headers every later call of this session needs."""
    headers = dict(HEADERS)
    opened = await ctx.net.request(
        source,
        "POST",
        url,
        headers=dict(headers),
        queue=queue,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "travel-ops", "version": "0.1.0"},
            },
        },
    )
    if opened.status >= 500:
        raise SourceFault(f"{source} answered HTTP {opened.status}")
    protocol = envelope(opened.body).get("result", {}).get("protocolVersion")
    if protocol:
        headers["mcp-protocol-version"] = protocol
    session = next((v for k, v in opened.headers.items() if k.lower() == "mcp-session-id"), None)
    if session:
        headers["mcp-session-id"] = session
    await ctx.net.request(
        source,
        "POST",
        url,
        headers=dict(headers),
        queue=queue,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    return headers


async def call_tool(
    ctx: Context, source: str, url: str, headers: dict, tool: str, arguments: dict, ident: int = 2, timeout=60
):
    """What the tool returned: its structured content, or the JSON in its text. Text that is not JSON is the
    server talking to a model and is not passed on."""
    response = await ctx.net.request(
        source,
        "POST",
        url,
        headers=dict(headers),
        json={"jsonrpc": "2.0", "id": ident, "method": "tools/call", "params": {"name": tool, "arguments": arguments}},
        timeout=timeout,
    )
    if response.status >= 500:
        raise SourceFault(f"{source} answered HTTP {response.status}")
    if response.status != 200:
        raise ParseError(f"{source} answered HTTP {response.status}")
    answer = envelope(response.body)
    if "error" in answer:
        raise ParseError(f"{source} RPC error: {answer['error'].get('message', 'unknown error')}")
    result = answer.get("result") or {}
    texts = [item.get("text", "") for item in result.get("content") or [] if item.get("type") == "text"]
    if result.get("isError"):
        raise Refused(f"{source} refused the query: {'; '.join(texts)[:300]}")
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    for text in texts:
        try:
            return json.loads(text)
        except ValueError:
            continue
    raise ParseError(f"{source} answered without data")
