import pytest

from travelops.net.browser import BrowserSessions, Session
from travelops.net.client import Blocked


class FakeLauncher:
    def __init__(self, cookies):
        self.cookies, self.calls = cookies, 0

    async def __call__(self, engine, url, ready_cookie, proxy, headless):
        self.calls += 1
        if ready_cookie and ready_cookie not in self.cookies:
            raise Blocked("anti-bot did not issue a session")
        return Session(cookies=dict(self.cookies), user_agent="UA", engine=engine, obtained=0)


async def test_session_is_cached_on_disk(tmp_path):
    launcher = FakeLauncher({"aws-waf-token": "t"})
    s = BrowserSessions(tmp_path, exit_="", proxy=None, launcher=launcher, clock=lambda: 100.0)
    first = await s.get("booking", "https://www.booking.com", engine="chromium", ready_cookie="aws-waf-token")
    again = BrowserSessions(tmp_path, exit_="", proxy=None, launcher=launcher, clock=lambda: 200.0)
    second = await again.get("booking", "https://www.booking.com", engine="chromium", ready_cookie="aws-waf-token")
    assert first.cookies == second.cookies and launcher.calls == 1


async def test_expired_session_is_refreshed(tmp_path):
    launcher = FakeLauncher({"c": "1"})
    now = [0.0]
    s = BrowserSessions(tmp_path, exit_="", proxy=None, launcher=launcher, clock=lambda: now[0])
    await s.get("x", "u", engine="camoufox", max_age=60)
    now[0] = 61
    await s.get("x", "u", engine="camoufox", max_age=60)
    assert launcher.calls == 2


async def test_drop_forces_new_session(tmp_path):
    launcher = FakeLauncher({"c": "1"})
    s = BrowserSessions(tmp_path, exit_="", proxy=None, launcher=launcher, clock=lambda: 0.0)
    await s.get("x", "u", engine="camoufox")
    s.drop("x")
    await s.get("x", "u", engine="camoufox")
    assert launcher.calls == 2


async def test_missing_cookie_is_blocked(tmp_path):
    s = BrowserSessions(tmp_path, exit_="", proxy=None, launcher=FakeLauncher({}), clock=lambda: 0.0)
    with pytest.raises(Blocked):
        await s.get("booking", "u", engine="chromium", ready_cookie="aws-waf-token")


def test_impersonation_matches_engine():
    assert Session({}, "UA", "chromium", 0).impersonate == "chrome"
    assert Session({}, "UA", "camoufox", 0).impersonate == "firefox"


async def test_browser_warmup_obeys_quarantine(tmp_path):
    from collections import Counter
    from travelops.net.limiter import Limiter, Quarantined, Rule

    limiter = Limiter(None, default=Rule(interval=0, jitter=0), clock=lambda: 100.0)
    limiter.blocked("x")
    launcher = FakeLauncher({"c": "1"})
    sessions = BrowserSessions(tmp_path, exit_="", proxy=None, launcher=launcher, limiter=limiter, counts=Counter())
    with pytest.raises(Quarantined):
        await sessions.get("x", "u", engine="camoufox")
    assert launcher.calls == 0


async def test_concurrent_warmups_share_one_launch_and_request_budget(tmp_path):
    import asyncio
    from collections import Counter
    from travelops.net.limiter import Limiter, Rule

    launcher = FakeLauncher({"c": "1"})
    counts = Counter()
    limiter = Limiter(None, default=Rule(interval=0, jitter=0))
    sessions = BrowserSessions(tmp_path, exit_="", proxy=None, launcher=launcher, limiter=limiter, counts=counts)
    await asyncio.gather(*(sessions.get("x", "u", engine="camoufox") for _ in range(2)))
    assert launcher.calls == 1 and counts["x"] == 1


async def test_refused_navigation_does_not_harvest_a_session():
    from travelops.net.browser import _harvest

    class Page:
        async def evaluate(self, expression):
            return "test-agent"

        async def goto(self, *args, **kw):
            return type("Response", (), {"status": 403})()

    class Browser:
        async def new_page(self):
            return Page()

    with pytest.raises(Blocked, match="403"):
        await _harvest(Browser(), "camoufox", "https://example.test", None)


async def test_user_agent_is_read_before_redirect_can_replace_context():
    from travelops.net.browser import _harvest

    class Cookies:
        async def cookies(self):
            return [{"name": "ready", "value": "test"}]

    class Page:
        context = Cookies()
        navigated = False

        async def evaluate(self, expression):
            assert not self.navigated
            return "stable-agent"

        async def goto(self, *args, **kw):
            self.navigated = True
            return type("Response", (), {"status": 200})()

    class Browser:
        async def new_page(self):
            return Page()

    result = await _harvest(Browser(), "chromium", "https://example.test", "ready")
    assert result.user_agent == "stable-agent" and result.cookies == {"ready": "test"}


async def test_chrome_sandbox_is_asked_for_by_the_environment(monkeypatch):
    """Playwright starts Chrome with --no-sandbox unless told otherwise. In a container that allows it, the
    sandbox is turned on by TRAVELOPS_CHROME_SANDBOX=1; the default stays as it was."""
    import playwright.async_api as api

    from travelops.net import browser

    seen = []

    class Stop(Exception):
        pass

    class Chromium:
        async def launch(self, **kwargs):
            seen.append(kwargs)
            raise Stop

    class Playwright:
        chromium = Chromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(api, "async_playwright", lambda: Playwright())
    for value in (None, "1"):
        if value:
            monkeypatch.setenv("TRAVELOPS_CHROME_SANDBOX", value)
        with pytest.raises(Stop):
            await browser.launch("chromium", "https://example.test", None, None, True)
    assert [kw["chromium_sandbox"] for kw in seen] == [False, True]
    assert all(kw["channel"] == "chrome" for kw in seen)
