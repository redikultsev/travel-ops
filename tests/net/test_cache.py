from travelops.net.cache import RawCache


def test_put_get_and_expiry(tmp_path):
    now = [100.0]
    cache = RawCache(tmp_path / "c.sqlite", clock=lambda: now[0])
    key = RawCache.key("GET", "https://x", {"a": 1}, None)
    cache.put(key, "src", "https://x", 200, b"body")
    assert cache.get(key, max_age=10).body == b"body"
    now[0] += 11
    assert cache.get(key, max_age=10) is None


def test_key_depends_on_body():
    assert RawCache.key("POST", "u", None, {"a": 1}) != RawCache.key("POST", "u", None, {"a": 2})


def test_latest_for_repair(tmp_path):
    cache = RawCache(tmp_path / "c.sqlite")
    cache.put("k1", "tutu", "u1", 200, b"one")
    cache.put("k2", "tutu", "u2", 200, b"two")
    assert [r.body for r in cache.latest("tutu", 5)] == [b"two", b"one"]
