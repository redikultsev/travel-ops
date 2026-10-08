"""What a search card does not say about a stay: amenities, exact place, house rules, more photos. Each stay is one
request to its property page, so details are read for the few stays worth it, and kept for a month."""

from __future__ import annotations

import asyncio
import re

from .memory import Stored
from .net.browser import BrowserUnavailable
from .net.client import Blocked, Offline
from .net.limiter import Quarantined
from .sources import STAY_SOURCES
from .sources.base import NotConfigured, ParseError, SourceFault

AT_MOST = 5
PHOTOS_AT_MOST = 8


def pick(stored: Stored, asked: list[str]) -> list[tuple[str, dict | None]]:
    """Cards of a search by name or source id. A name may be given in part if it fits one stay only."""
    cards = stored.result["cards"]
    found = []
    for name in asked:
        key = str(name).strip().casefold()
        exact = [c for c in cards if any(key in (s["source_id"].casefold(), s["name"].casefold()) for s in listings(c))]
        partial = [c for c in cards if key and any(key in s["name"].casefold() for s in listings(c))]
        match = exact or (partial if len(partial) == 1 else [])
        found.append((name, match[0] if match else None))
    return found


def listings(card: dict) -> list[dict]:
    """The property as each source lists it; a search stored before sources were merged has one."""
    return card.get("listed_on") or [card["stay"]]


def own_link(card: dict) -> str:
    """The property page on the card's own source: the cheapest rate may be another source's."""
    source = card["stay"]["source"]
    return next((rate for rate in card["rates"] if rate.get("source", source) == source), card["rates"][0])["link"]["url"]


def summary(card: dict) -> dict:
    stay, rate = card["stay"], card["rates"][0]
    return {
        "name": stay["name"],
        "source": stay["source"],
        "rating": stay["rating"],
        "reviews": stay["reviews"],
        "cheapest_rate": {k: rate.get(k) for k in ("total", "extra_charges", "all_in", "room", "link", "seen_at")},
    }


async def read_details(app, stored: Stored, asked: list[str], refresh: bool = False) -> list[dict]:
    if not asked or len(asked) > AT_MOST:
        raise ValueError(f"name from 1 to {AT_MOST} stays; each one is a request to its property page")
    place = stored.result["query"]["place"]
    out = []
    for name, card in pick(stored, asked):
        if card is None:
            out.append({"asked": name, "status": "not_found", "reason": "no single stay of this search has this name"})
            continue
        stay = card["stay"]
        entry = summary(card)
        known = None if refresh else app.results.details(stay["source"], stay["source_id"])
        entry["from_memory"] = known is not None
        if known is None:
            source = STAY_SOURCES[stay["source"]]()
            try:
                raw = await asyncio.wait_for(
                    source.fetch_details(own_link(card), place, app.ctx), timeout=150
                )
                known = app.results.put_details(stay["source"], stay["source_id"], source.parse_details(raw))
            except (Blocked, Quarantined) as exc:
                entry.update(status="blocked", reason=str(exc))
            except (TimeoutError, asyncio.TimeoutError) as exc:
                entry.update(status="timeout", reason=str(exc) or "no answer in 150 s")
            except ParseError as exc:
                entry.update(status="unparsed", reason=f"page not understood: {exc}")
            except (NotConfigured, BrowserUnavailable) as exc:
                entry.update(status="not_configured", reason=str(exc))
            except (SourceFault, Offline) as exc:
                entry.update(status="failed", reason=str(exc))
        if known is not None:
            photos = distinct(known.get("photos", []) + stay["photos"])
            entry.update(status="ok", **{k: v for k, v in known.items() if k != "photos"})
            entry["photos"], entry["photos_total"] = photos[:PHOTOS_AT_MOST], len(photos)
            if known.get("kind") in (None, "other"):
                entry["kind"] = stay["kind"]
        out.append(entry)
    return out


def sized(url: str) -> tuple[str, dict | None]:
    """A photo large enough to judge a room by and small enough to send several."""
    if "bstatic.com" in url:
        return url.replace("/max1024x768/", "/max500/"), None
    if "muscache.com" in url:
        return url, {"im_w": 480}
    return url, None


def distinct(urls: list[str]) -> list[str]:
    """One link per picture: a site serves the same photo in several sizes under one id."""
    seen, out = set(), []
    for url in urls:
        key = re.sub(r"/(square\d+|max\d+x?\d*)/", "/", url.split("?")[0]).rsplit(".", 1)[0]
        if key not in seen:
            seen.add(key)
            out.append(url)
    return out


def image_format(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


async def read_photos(app, stored: Stored, asked: list[str], per_stay: int = 2) -> list[dict]:
    """Photos of the named stays as bytes, for an agent that can look at them. Property pages are not opened:
    photos come from the search card, plus the page's own when `stay_details` has read it."""
    if not asked or len(asked) > AT_MOST:
        raise ValueError(f"name from 1 to {AT_MOST} stays")
    if type(per_stay) is not int or not 1 <= per_stay <= 4:
        raise ValueError("per_stay must be from 1 to 4")
    out = []
    for name, card in pick(stored, asked):
        if card is None:
            out.append({"asked": name, "status": "not_found"})
            continue
        stay = card["stay"]
        known = app.results.details(stay["source"], stay["source_id"]) or {}
        # The page's own photos first: they are larger than the thumbnail of a search card.
        urls = distinct(known.get("photos", []) + stay["photos"])
        entry = {"name": stay["name"], "source": stay["source"], "photos_total": len(urls), "photos": []}
        for url in urls[:per_stay]:
            address, params = sized(url)
            try:
                # Formats an agent can look at: without this a CDN picks AVIF for a browser-like client.
                response = await app.net.request(
                    "images", "GET", address, params=params, headers={"accept": "image/jpeg,image/png,image/webp"}
                )
                kind = image_format(response.body)
                if response.status != 200 or kind is None or len(response.body) > 1_500_000:
                    reason = f"HTTP {response.status}" if response.status != 200 else "not a JPEG, PNG, WebP or GIF"
                    entry["photos"].append({"url": url, "status": "failed", "reason": reason})
                else:
                    entry["photos"].append({"url": url, "status": "ok", "format": kind, "data": response.body})
            except (Blocked, Quarantined, TimeoutError, Offline) as exc:
                entry["photos"].append({"url": url, "status": "failed", "reason": str(exc)})
        out.append(entry)
    return out
