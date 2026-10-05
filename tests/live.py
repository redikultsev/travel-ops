"""Manual checks share the real persistent budget; a quarantined source is skipped until its rest is over."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from travelops.core.report import Status
from travelops.net.browser import BrowserSessions
from travelops.net.client import Net
from travelops.net.limiter import Limiter
from travelops.search import run_source
from travelops.sources import RULES
from travelops.sources.base import Context

ROOT = Path(__file__).parents[1]


async def check_source(source, query):
    data = ROOT / "data"
    data.mkdir(exist_ok=True)
    limiter = Limiter(data / "limiter.sqlite", RULES)
    net = Net(data, limiter=limiter)
    browser = BrowserSessions(data, exit_=net.exit, proxy=None, limiter=limiter, counts=net.counts)
    try:
        offers, report = await run_source(source, query, Context(net, browser, lambda: datetime.now(timezone.utc)), 180)
        if report.status in (Status.BLOCKED, Status.TIMEOUT):
            pytest.skip(f"{source.name}: {report.status}: {report.reason}")
        assert report.status is Status.OK, report.reason or str(report.status)
        assert offers
        return offers
    finally:
        await net.close()
        net.cache.db.close()
        limiter.db.close()
