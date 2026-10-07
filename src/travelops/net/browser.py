"""Anti-bot sessions: a real browser passes the challenge once, the cookies serve plain HTTP afterwards.
Sessions are bound to the exit address (cookies are issued per IP), stored on disk and refreshed when stale."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable, Literal

from .client import Blocked
from .limiter import Limiter
from .tally import count

Engine = Literal["chromium", "camoufox"]
READY_TIMEOUT_S = 45


class BrowserUnavailable(Exception):
    """The browser a source needs is not installed. Not a refusal by the site: nothing is quarantined."""


@dataclass
class Session:
    cookies: dict[str, str]
    user_agent: str
    engine: str
    obtained: float

    @property
    def impersonate(self) -> str:
        return "chrome" if self.engine == "chromium" else "firefox"


Launcher = Callable[[str, str, str | None, str | None, bool], Awaitable[Session]]


async def launch(engine: str, url: str, ready_cookie: str | None, proxy: str | None, headless: bool) -> Session:
    if engine == "chromium":
        # The installed Google Chrome, not the bundled Chromium: AWS WAF hands its token to the former and
        # serves the latter a challenge it never passes. Recipe: fare-scraper (MIT), see NOTICE.
        from playwright.async_api import Error, async_playwright

        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(
                    channel="chrome", headless=headless, proxy={"server": proxy} if proxy else None
                )
            except Error as exc:
                raise BrowserUnavailable("Google Chrome is not installed; install it to use this source") from exc
            context = await browser.new_context(locale="en-GB")
            return await _harvest(context, engine, url, ready_cookie)
    from camoufox.async_api import AsyncCamoufox

    async with AsyncCamoufox(headless=headless, proxy={"server": proxy} if proxy else None, geoip=True) as browser:
        return await _harvest(browser, engine, url, ready_cookie)


Capturer = Callable[[str, str, str, str | None, bool], Awaitable[list[bytes]]]


async def capture(engine: str, url: str, pattern: str, proxy: str | None, headless: bool) -> list[bytes]:
    """Open a page as a person would and keep the bodies of the answers it receives from URLs matching `pattern`,
    once the page has had one. For a site whose requests the page signs itself: the browser makes the search, and
    nothing it sends is forged. Camoufox only."""
    from camoufox.async_api import AsyncCamoufox

    wanted, bodies, arrived = re.compile(pattern), [], asyncio.Event()

    async with AsyncCamoufox(headless=headless, proxy={"server": proxy} if proxy else None, geoip=True) as browser:
        page = await browser.new_page()

        async def keep(response):
            if wanted.search(response.url):
                try:
                    bodies.append(await response.body())
                    arrived.set()
                except Exception:  # an answer the page dropped before it finished
                    pass

        page.on("response", keep)
        first = await page.goto(url, wait_until="domcontentloaded")
        if first is not None and first.status in (403, 429, 451):
            raise Blocked(f"browser navigation refused with HTTP {first.status}")
        try:
            await asyncio.wait_for(arrived.wait(), READY_TIMEOUT_S)
        except TimeoutError:
            raise Blocked("the page did not search: an anti-bot check may have stopped it") from None
        await page.wait_for_timeout(2000)  # a streamed answer may still be finishing
    return bodies


async def _harvest(browser, engine: str, url: str, ready_cookie: str | None) -> Session:
    page = await browser.new_page()
    user_agent = await page.evaluate("navigator.userAgent")
    response = await page.goto(url, wait_until="domcontentloaded")
    if response is not None and response.status in (403, 429, 451):
        raise Blocked(f"browser navigation refused with HTTP {response.status}")
    deadline = time.monotonic() + READY_TIMEOUT_S
    while True:
        cookies = {c["name"]: c["value"] for c in await page.context.cookies()}
        if ready_cookie is None or ready_cookie in cookies:
            break
        if time.monotonic() > deadline:
            raise Blocked("anti-bot did not issue a session")
        await asyncio.sleep(1)
    return Session(cookies, user_agent, engine, time.time())


class BrowserSessions:
    def __init__(
        self,
        data_dir: Path,
        *,
        exit_: str,
        proxy: str | None,
        launcher: Launcher = launch,
        capturer: Capturer = capture,
        clock: Callable[[], float] = time.time,
        limiter: Limiter | None = None,
        counts: Counter | None = None,
    ) -> None:
        self.dir = data_dir / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.exit, self.proxy, self.launcher, self.clock = exit_, proxy, launcher, clock
        self.capturer = capturer
        self.limiter, self.counts = limiter, counts
        self.locks: dict[str, asyncio.Lock] = {}

    def _path(self, source: str) -> Path:
        return self.dir / f"{source}@{self.exit or 'direct'}.json".replace(":", "_")

    async def get(
        self,
        source: str,
        url: str,
        *,
        engine: Engine,
        ready_cookie: str | None = None,
        max_age: float = 3600,
        headless: bool = True,
    ) -> Session:
        async with self.locks.setdefault(source, asyncio.Lock()):
            path = self._path(source)
            if path.exists():
                session = Session(**json.loads(path.read_text()))
                if session.engine == engine and self.clock() - session.obtained <= max_age:
                    return session
            bucket = Limiter.bucket(source, self.exit)
            if self.limiter is not None:
                await self.limiter.acquire(bucket)
            count(self.counts, source)
            try:
                session = await self.launcher(engine, url, ready_cookie, self.proxy, headless)
            except Blocked:
                if self.limiter is not None:
                    self.limiter.blocked(bucket)
                raise
            session.obtained = self.clock()
            path.touch(mode=0o600, exist_ok=True)
            path.write_text(json.dumps(asdict(session)))
            return session

    async def search(self, source: str, url: str, pattern: str, *, headless: bool = True) -> list[bytes]:
        """A search the page makes itself: open `url` in a browser and return the answers it got from URLs
        matching `pattern`. One page is one request of the source, spaced and quarantined like any other."""
        bucket = Limiter.bucket(source, self.exit)
        if self.limiter is not None:
            await self.limiter.acquire(bucket)
        count(self.counts, source)
        try:
            bodies = await self.capturer("camoufox", url, pattern, self.proxy, headless)
        except Blocked:
            if self.limiter is not None:
                self.limiter.blocked(bucket)
            raise
        if self.limiter is not None:
            self.limiter.succeeded(bucket)
        return bodies

    def drop(self, source: str) -> None:
        self._path(source).unlink(missing_ok=True)
