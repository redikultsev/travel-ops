"""What the container image is built from and runs. Checked by reading the files: no Docker needed."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def instructions() -> list[tuple[str, str]]:
    text = re.sub(r"\\\n\s*", " ", (ROOT / "Dockerfile").read_text())
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    return [tuple(line.split(None, 1)) for line in lines]


def test_the_image_is_pinned_and_runs_the_http_server_as_nobody_special():
    steps = instructions()
    assert steps[0][0] == "FROM" and re.search(r"@sha256:[0-9a-f]{64}$", steps[0][1]), "the base image by digest"
    users = [arg for op, arg in steps if op == "USER"]
    assert users and users[-1] not in ("root", "0"), "browsers that open strangers' pages do not run as root"
    assert steps[-1] == ("CMD", '["travelops", "mcp", "--http", "--host", "0.0.0.0", "--port", "8765"]')
    assert ("ENV", "TRAVELOPS_DATA=/data") in steps, "searches, watches and limits live on the volume"


def test_both_browsers_and_nothing_personal():
    steps = instructions()
    runs = " ".join(arg for op, arg in steps if op == "RUN")
    args = dict(arg.split("=", 1) for op, arg in steps if op == "ARG")
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+-1", args["CHROME_VERSION"]), "a real Chrome, of one version"
    assert re.fullmatch(r"[0-9a-f]{64}", args["CHROME_SHA256"]) and "sha256sum -c" in runs, "checked before install"
    assert "google-chrome-stable_${CHROME_VERSION}_amd64.deb" in runs and "--with-deps chrome" not in runs
    assert re.fullmatch(r"official/stable/\d+\.\d+(\.\d+)?-beta\.\d+", args["CAMOUFOX_VERSION"])
    assert "playwright install-deps firefox" in runs and "camoufox set ${CAMOUFOX_VERSION}" in runs, \
        "Camoufox is a Firefox, of one release"
    assert "camoufox fetch" not in runs, "not «whatever is newest» at build time"
    assert "install chromium" not in runs, "Playwright's own Chromium is not used: 650 MB for nothing"
    copied = [arg.split()[:-1] for op, arg in steps if op == "COPY"]
    assert copied == [["pyproject.toml", "uv.lock"], ["src"]], "the code and its lock, nothing else"
    ignored = (ROOT / ".dockerignore").read_text().split()
    assert {"/profile.yml", "/data", ".venv", ".git"} <= set(ignored), "the profile and the data stay out"
