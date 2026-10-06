"""A public MCP server spoken to over plain HTTP. A source names the search tool it calls and gets back what the
tool returned; the handshake, the JSON-RPC envelope, server faults, refusals and text the server addresses to a
model stay here. Only search tools are ever named by a source; whatever else a server offers is never called."""

from __future__ import annotations

import asyncio
import json

from .base import Context, ParseError, SourceFault

HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream"}


class Refused(ParseError):
    """The tool answered, with a refusal in words: an unknown place, a date out of range."""


def envelope(raw: bytes) -> dict:
    """The JSON-RPC answer, sent either as JSON or as the last `data:` frame of an event stream."""
    try:
        text = raw.decode().strip()
        if not text.startswith("{"):
            frames = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
            text = next(frame for frame in reversed(frames) if frame.startswith("{"))
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except (ValueError, StopIteration) as exc:
        raise ParseError("answer is neither JSON-RPC JSON nor an event stream") from exc


class Server:
    """One source's line to one server. A source object lives for one search, and every route and date of that
    search is a run of it: they share one handshake. A handshake that failed is not kept, so the next run tries
    again. `handshake=False` is for a server that keeps no session. `queue` is the limiter's line for the
    handshake, so that it does not wait behind searches."""

    def __init__(self, source: str, url: str, *, handshake: bool = True, queue: str | None = None) -> None:
        self.source, self.url, self.handshake, self.queue = source, url, handshake, queue
        self._opened: asyncio.Task | None = None
        self._ids = iter(range(2, 1_000_000))

    async def _open(self, ctx: Context) -> dict:
        headers = dict(HEADERS)
        opened = await ctx.net.request(
            self.source,
            "POST",
            self.url,
            headers=dict(headers),
            queue=self.queue,
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
            raise SourceFault(f"{self.source} answered HTTP {opened.status}")
        protocol = envelope(opened.body).get("result", {}).get("protocolVersion")
        if protocol:
            headers["mcp-protocol-version"] = protocol
        session = next((v for k, v in opened.headers.items() if k.lower() == "mcp-session-id"), None)
        if session:
            headers["mcp-session-id"] = session
        await ctx.net.request(
            self.source,
            "POST",
            self.url,
            headers=dict(headers),
            queue=self.queue,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        return headers

    async def _headers(self, ctx: Context) -> dict:
        if not self.handshake:
            return dict(HEADERS)
        if self._opened is None or (self._opened.done() and (self._opened.cancelled() or self._opened.exception())):
            self._opened = asyncio.ensure_future(self._open(ctx))
        return await asyncio.shield(self._opened)

    async def call(self, ctx: Context, tool: str, arguments: dict, timeout: float = 60):
        """What the tool returned: its structured content, or the JSON in its text. Text that is not JSON is the
        server talking to a model and is not passed on. Raises `Refused` when the tool says no in words."""
        response = await ctx.net.request(
            self.source,
            "POST",
            self.url,
            headers=dict(await self._headers(ctx)),
            json={
                "jsonrpc": "2.0",
                "id": next(self._ids),
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
            timeout=timeout,
        )
        if response.status >= 500:
            raise SourceFault(f"{self.source} answered HTTP {response.status}")
        if response.status != 200:
            raise ParseError(f"{self.source} answered HTTP {response.status}")
        answer = envelope(response.body)
        if "error" in answer:
            raise ParseError(f"{self.source} RPC error: {answer['error'].get('message', 'unknown error')}")
        result = answer.get("result") or {}
        texts = [item.get("text", "") for item in result.get("content") or [] if item.get("type") == "text"]
        if result.get("isError"):
            raise Refused(f"{self.source} refused the query: {'; '.join(texts)[:300]}")
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        for text in texts:
            try:
                return json.loads(text)
            except ValueError:
                continue
        raise ParseError(f"{self.source} answered without data")
