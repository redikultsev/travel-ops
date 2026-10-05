from pathlib import Path

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
