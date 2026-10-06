"""Application resources shared by CLI and MCP."""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from .memory import Results
from .net.client import Net
from .net.browser import BrowserSessions
from .net.limiter import Limiter
from .profile import Profile, data_dir, load_profile
from .sources import RULES
from .sources.base import Context
from .watch import Watches


@dataclass
class App:
    root: Path
    net: Net
    browser: BrowserSessions
    ctx: Context
    limiter: Limiter
    profile: Profile
    results: Results
    replay: bool = False  # answers come from a recording; nothing goes to the network
    watches: Watches | None = None  # saved searches that `travelops watch run` repeats
    closed: bool = False

    async def close(self):
        if not self.closed:
            try:
                await self.net.close()
            finally:
                self.net.cache.db.close()
                self.limiter.db.close()
                self.results.db.close()
                if self.watches is not None:
                    self.watches.db.close()
                self.closed = True


def build(root: Path, proxy: str | None = None) -> App:
    if os.environ.get("TRAVELOPS_REPLAY"):
        from .replay import build_replay

        return build_replay(Path(os.environ["TRAVELOPS_REPLAY"]))
    root = Path(root).resolve()
    profile = load_profile(root)
    data = data_dir(root)
    data.mkdir(parents=True, exist_ok=True)
    limiter = Limiter(data / "limiter.sqlite", RULES)
    net = Net(data, proxy=proxy, limiter=limiter)
    browser = BrowserSessions(data, exit_=net.exit, proxy=net.proxy, limiter=limiter, counts=net.counts)
    return App(
        root,
        net,
        browser,
        Context(net, browser, lambda: datetime.now(timezone.utc)),
        limiter,
        profile,
        Results(data / "results.sqlite"),
        watches=Watches(data / "watches.sqlite"),
    )
