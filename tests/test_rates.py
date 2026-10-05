import json

from travelops.net.cache import RawCache
from travelops.net.client import Blocked, Net
from travelops.net.limiter import Limiter, Rule
from travelops.rates import RATES_URL, UNAVAILABLE, load_rates

FEED = {"result": "success", "base_code": "EUR", "time_last_update_utc": "day", "rates": {"EUR": 1, "RUB": 95}}


class Offline(Net):
    def __init__(self, tmp_path):
        super().__init__(tmp_path, limiter=Limiter(None, default=Rule(interval=0, jitter=0)), cache=RawCache(None))

    async def _send(self, method, url, **kw):
        raise Blocked("feed is down")


async def test_a_dead_feed_falls_back_to_the_last_saved_table(tmp_path):
    net = Offline(tmp_path)
    net.cache.put("old", "rates", RATES_URL, 200, json.dumps(FEED).encode())
    rates = await load_rates(net)
    assert rates.day == "day" and "RUB" in rates.per_base


async def test_no_table_at_all_means_no_conversion_not_no_search(tmp_path):
    rates = await load_rates(Offline(tmp_path))
    assert rates.day == UNAVAILABLE and set(rates.per_base) == {"EUR"}
