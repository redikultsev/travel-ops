"""An application that answers from a recording. For checking an agent against searches that really happened,
without touching a travel site: the clock stands at the moment of the recording and the network is closed."""

from __future__ import annotations

import gzip
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .memory import Results
from .net.browser import BrowserSessions
from .net.cache import RawCache
from .net.client import Net
from .net.limiter import Limiter
from .profile import load_profile
from .sources import RULES
from .sources.base import Context


def read_json(path: Path):
    return json.loads(gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes())


def write_json(path: Path, value) -> None:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    # mtime=0: the same recording always makes the same file.
    path.write_bytes(gzip.compress(data, mtime=0) if path.suffix == ".gz" else data)


def load_scenario(directory: Path) -> dict:
    scenario = yaml.safe_load((directory / "scenario.yml").read_text())
    if not isinstance(scenario, dict) or "now" not in scenario or not scenario.get("turns"):
        raise ValueError(f"{directory}/scenario.yml needs `now` and `turns`")
    return scenario


def build_replay(directory: Path):
    from .app import App

    directory = Path(directory).resolve()
    # TRAVELOPS_REPLAY_MINUTES moves the clock on: a turn that happens an hour later meets prices an hour old.
    later = timedelta(minutes=float(os.environ.get("TRAVELOPS_REPLAY_MINUTES") or 0))
    moment = datetime.fromisoformat(str(load_scenario(directory)["now"])) + later
    now = moment.timestamp()
    scratch = Path(tempfile.mkdtemp(prefix="travelops-replay-"))
    limiter = Limiter(scratch / "limiter.sqlite", RULES)
    cache = RawCache(None, clock=lambda: now)
    for row in read_json(directory / "raw.json") if (directory / "raw.json").exists() else []:
        cache.put(row["key"], row["source"], row["url"], row["status"], row["body"].encode())
    net = Net(scratch, limiter=limiter, cache=cache, offline=True)
    results = Results(None, clock=lambda: now)
    for path in sorted((directory / "memory").glob("*.json*")):
        row = read_json(path)
        results.load(row["id"], row["key"], row["kind"], row["at"], row["result"])
    if (directory / "details.json").exists():
        for row in read_json(directory / "details.json"):
            results.put_details(row["source"], row["source_id"], row["details"])
    browser = BrowserSessions(scratch, exit_=net.exit, proxy=net.proxy, limiter=limiter, counts=net.counts)
    context = Context(net, browser, lambda: moment.astimezone(timezone.utc))
    return App(directory, net, browser, context, limiter, load_profile(directory), results, replay=True)
