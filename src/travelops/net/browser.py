"""Anti-bot sessions: a real browser passes the challenge once, the cookies serve plain HTTP afterwards.
Sessions are bound to the exit address (cookies are issued per IP), stored on disk and refreshed when stale."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable, Literal

from .client import Blocked
from .limiter import Limiter, Quarantined
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
                    channel="chrome",
                    headless=headless,
                    proxy={"server": proxy} if proxy else None,
                    # Off by default, as Playwright has it: Chrome's sandbox needs user namespaces, which a
                    # container may not grant. Where it works, TRAVELOPS_CHROME_SANDBOX=1 turns it on.
                    chromium_sandbox=os.environ.get("TRAVELOPS_CHROME_SANDBOX") == "1",
                )
            except Error as exc:
                raise BrowserUnavailable("Google Chrome is not installed; install it to use this source") from exc
            context = await browser.new_context(locale="en-GB")
            return await _harvest(context, engine, url, ready_cookie)
    from camoufox.async_api import AsyncCamoufox

    async with AsyncCamoufox(headless=headless, proxy={"server": proxy} if proxy else None, geoip=True) as browser:
        return await _harvest(browser, engine, url, ready_cookie)


Capturer = Callable[..., Awaitable[list[bytes]]]


async def capture(
    engine: str,
    url: str,
    pattern: str,
    proxy: str | None,
    headless: bool,
    typed: tuple[str, str] | None = None,
    scrolls: int = 0,
    until=None,
    patience: float = 0,
) -> list[bytes]:
    """Open a page as a person would and keep the bodies of the answers it receives from URLs matching `pattern`,
    once the page has had one. For a site whose requests the page signs itself: the browser makes the search, and
    nothing it sends is forged. `typed` is a field and the text a person would type into it, for an answer that
    comes only while one types, such as a search box's suggestions. `until(bodies)` says when a search the page
    keeps polling has finished; the page is watched for up to `patience` seconds for it. Camoufox only."""
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
        if typed:
            # A field typed into before the page's scripts are ready takes the text and asks nothing.
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:  # a page that never falls quiet: type anyway
                pass
            field = page.locator(typed[0]).first
            await field.click(timeout=READY_TIMEOUT_S * 1000)
            await page.wait_for_timeout(1500)
            await field.type(typed[1], delay=120)
        try:
            await asyncio.wait_for(arrived.wait(), READY_TIMEOUT_S)
        except TimeoutError:
            raise Blocked("the page did not search: an anti-bot check may have stopped it") from None
        await page.wait_for_timeout(2000)  # a streamed answer may still be finishing
        if until is not None:
            for _ in range(int(patience * 2)):
                if until(bodies):
                    break
                await page.wait_for_timeout(500)
        # A list that grows as a person scrolls: scroll to its end until it stops growing or `scrolls` is spent.
        # A wheel alone stops short of the end, where the next page is asked for (Trip.com, 2026-10-08).
        if scrolls:
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:  # a page that never falls quiet: scroll anyway
                pass
        for _ in range(scrolls):
            before = len(bodies)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.mouse.wheel(0, 3000)
            for _ in range(16):
                await page.wait_for_timeout(500)
                if len(bodies) > before:
                    break
            if len(bodies) == before:
                break
            await page.wait_for_timeout(1500)
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

    async def search(
        self,
        source: str,
        url: str,
        pattern: str,
        *,
        headless: bool = True,
        typed: tuple[str, str] | None = None,
        queue: str | None = None,
        scrolls: int = 0,
        until=None,
        patience: float = 0,
    ) -> list[bytes]:
        """A search the page makes itself: open `url` in a browser and return the answers it got from URLs
        matching `pattern`. One page is one request of the source, spaced and quarantined like any other; `queue`
        is a line of its own, as for HTTP requests."""
        bucket = Limiter.bucket(f"{source}/{queue}" if queue else source, self.exit)
        if self.limiter is not None:
            main = Limiter.bucket(source, self.exit)
            if queue and (rest := self.limiter.state(main).quarantined_until) > self.limiter.clock():
                raise Quarantined(main, rest)
            await self.limiter.acquire(bucket)
        count(self.counts, source)
        try:
            extra = {
                **({"typed": typed} if typed else {}),
                **({"scrolls": scrolls} if scrolls else {}),
                **({"until": until, "patience": patience} if until else {}),
            }
            bodies = await self.capturer("camoufox", url, pattern, self.proxy, headless, **extra)
        except Blocked:
            if self.limiter is not None:
                self.limiter.blocked(bucket)
                if queue:  # a refusal on any line rests the whole site
                    self.limiter.blocked(Limiter.bucket(source, self.exit))
            raise
        if self.limiter is not None:
            self.limiter.succeeded(bucket)
        return bodies

    def drop(self, source: str) -> None:
        self._path(source).unlink(missing_ok=True)
