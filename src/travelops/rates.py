"""Currency rates for comparing prices. A missing feed must not cost the user the search itself."""

from __future__ import annotations

import logging

from .core.money import Rates, parse_open_er_api
from .net.client import Net, Response

RATES_URL = "https://open.er-api.com/v6/latest/EUR"
UNAVAILABLE = "unavailable"
log = logging.getLogger("travelops.rates")


async def load_rates(net: Net) -> Rates:
    try:
        resp = await net.request("rates", "GET", RATES_URL, cache_ttl=12 * 3600)
        return parse_open_er_api(resp.json())
    except Exception as exc:  # any failure of the feed: fall back, never fail the search
        log.warning("rates feed failed: %s", exc)
    for cached in net.cache.latest("rates", 1):
        try:
            return parse_open_er_api(Response(cached.status, cached.body).json())
        except ValueError:
            break
    # No table at all: prices stay in their own currencies and nothing is converted.
    return Rates("EUR", {}, day=UNAVAILABLE)
