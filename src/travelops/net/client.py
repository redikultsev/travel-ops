"""The only way to the network. Every request passes the rate limiter here, so a source cannot skip it; a block
is raised as `Blocked`, so "the site refused us" never looks like "no tickets"."""

from __future__ import annotations

import json as jsonlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import Timeout as TransportTimeout

from .cache import RawCache
from .limiter import Limiter, Quarantined


class Blocked(Exception):
    """The site refused us: banned address, anti-bot, captcha. The reason is shown to the user as is."""


class Offline(ValueError):
    """A replay was asked for something it did not record. Nothing goes to the network in a replay."""


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)
    url: str = ""

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        return jsonlib.loads(self.body)


def exit_of(proxy: str | None) -> str:
    if not proxy:
        return ""
    parts = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
    return f"{parts.hostname}:{parts.port}" if parts.port else str(parts.hostname)


def generic_block(resp: Response) -> str | None:
    if resp.status == 429:
        return "too many requests: the site throttled us"
    if resp.status in (403, 451):
        return "the site refused our address"
    return None


class Net:
    def __init__(
        self,
        data_dir: Path,
        *,
        proxy: str | None = None,
        limiter: Limiter | None = None,
        cache: RawCache | None = None,
        impersonate: str = "chrome",
        timeout: float = 30.0,
        offline: bool = False,
    ) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.proxy = proxy if not proxy or "://" in proxy else f"http://{proxy}"
        self.exit = exit_of(proxy)
        self.limiter = limiter or Limiter(data_dir / "limiter.sqlite")
        self.cache = cache or RawCache(data_dir / "raw.sqlite")
        self.impersonate, self.timeout, self.offline = impersonate, timeout, offline
        self.counts: Counter[str] = Counter()
        self._session: AsyncSession | None = None

    async def request(
        self,
        source: str,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json: object = None,
        data: object = None,
        headers: dict | None = None,
        cookies: dict | None = None,
        impersonate: str | None = None,
        cache_ttl: float = 0,
        timeout: float | None = None,
        blocked_if: Callable[[Response], str | None] | None = None,
        queue: str | None = None,
    ) -> Response:
        """`queue` names a second line of the same source with its own spacing, for requests that are not
        searches (a protocol handshake). It shares the fate of the source: a source that rests is not asked
        through any line, and a refusal on any line rests the source."""
        key = RawCache.key(method, url, params, json if json is not None else data)
        if self.offline:
            if hit := self.cache.get(key, float("inf")):
                return Response(hit.status, hit.body, {}, hit.url)
            raise Offline(f"the replay holds no answer for {method} {url}")
        if cache_ttl and (hit := self.cache.get(key, cache_ttl)):
            return Response(hit.status, hit.body, {}, hit.url)
        main = Limiter.bucket(source, self.exit)
        bucket = Limiter.bucket(f"{source}:{queue}", self.exit) if queue else main
        if queue and (until := self.limiter.state(main).quarantined_until) > self.limiter.clock():
            raise Quarantined(main, until)
        await self.limiter.acquire(bucket)
        self.counts[source] += 1
        resp = await self._send(
            method,
            url,
            params=params,
            json=json,
            data=data,
            headers=headers,
            cookies=cookies,
            impersonate=impersonate or self.impersonate,
            timeout=timeout,
        )
        if reason := generic_block(resp) or (blocked_if(resp) if blocked_if else None):
            for line in {bucket, main}:
                self.limiter.blocked(line)
            raise Blocked(reason)
        self.limiter.succeeded(bucket)
        self.cache.put(key, source, url, resp.status, resp.body)
        return resp

    async def _send(self, method: str, url: str, **kw) -> Response:
        """The single physical request. Tests replace this and keep the limiter in place."""
        if self._session is None:
            self._session = AsyncSession(proxy=self.proxy, timeout=self.timeout)
        try:
            r = await self._session.request(method, url, **{k: v for k, v in kw.items() if v is not None})
        except TransportTimeout as exc:
            waited = kw.get("timeout") or self.timeout
            raise TimeoutError(f"a request got no answer in {waited:.0f} s") from exc
        return Response(r.status_code, r.content, dict(r.headers), str(r.url))

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
