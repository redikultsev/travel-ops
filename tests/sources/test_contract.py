import inspect
from datetime import date
from pathlib import Path

from travelops.kinds import KINDS

FORBIDDEN = ("curl_cffi", "httpx", "aiohttp", "requests", "urllib.request", "socket", "playwright", "camoufox")
SOURCES = Path(__file__).parents[2] / "src" / "travelops" / "sources"


def test_sources_never_open_their_own_connections():
    offenders = []
    for path in SOURCES.rglob("*.py"):
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and any(
                f" {m}" in stripped or f" {m}." in stripped for m in FORBIDDEN
            ):
                offenders.append(f"{path.name}: {stripped}")
    assert not offenders, "sources must go through ctx.net / ctx.browser:\n" + "\n".join(offenders)


def test_every_source_of_every_kind_keeps_the_one_interface():
    for kind in KINDS.values():
        query = kind.sample(date(2026, 11, 14))
        for source in kind.sources():
            assert source.name in kind.registry, f"{source.name} is registered under another name"
            assert inspect.iscoroutinefunction(source.fetch) and not inspect.iscoroutinefunction(source.parse)
            ceiling = source.max_requests(query)
            assert isinstance(ceiling, int) and ceiling >= 1, source.name
            usual = getattr(source, "typical_requests", source.max_requests)(query)
            assert 0 <= usual <= ceiling, f"{source.name}: what a search usually costs is above its ceiling"
