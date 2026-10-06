import pytest

from travelops.net.cache import RawCache
from travelops.net.client import Blocked, Net, Response, exit_of
from travelops.net.limiter import Limiter, Rule


class FakeNet(Net):
    def __init__(self, tmp_path, replies, **kw):
        super().__init__(
            tmp_path, limiter=Limiter(None, default=Rule(interval=0, jitter=0)), cache=RawCache(None), **kw
        )
        self.replies, self.sent = list(replies), []

    async def _send(self, method, url, **kw):
        self.sent.append((method, url, kw))
        return self.replies.pop(0)


async def test_request_counts_and_returns(tmp_path):
    net = FakeNet(tmp_path, [Response(200, b'{"a": 1}', {}, "u")])
    resp = await net.request("src", "GET", "u")
    assert resp.json() == {"a": 1} and net.counts["src"] == 1


async def test_403_is_blocked_and_quarantines(tmp_path):
    net = FakeNet(tmp_path, [Response(403, b"denied", {}, "u")])
    with pytest.raises(Blocked):
        await net.request("src", "GET", "u")
    assert net.limiter.state("src").quarantined_until > 0


async def test_source_specific_block_check(tmp_path):
    net = FakeNet(tmp_path, [Response(202, b"<html>Challenge</html>", {}, "u")])
    with pytest.raises(Blocked, match="anti-bot"):
        await net.request(
            "src", "GET", "u", blocked_if=lambda r: "anti-bot challenge" if b"Challenge" in r.body else None
        )


async def test_cache_hit_skips_network(tmp_path):
    net = FakeNet(tmp_path, [Response(200, b"x", {}, "u")])
    await net.request("src", "GET", "u", cache_ttl=60)
    await net.request("src", "GET", "u", cache_ttl=60)
    assert len(net.sent) == 1


def test_exit_never_holds_credentials():
    assert exit_of("http://user:secret@proxy.example:10310") == "proxy.example:10310"
    assert exit_of("proxy.example:10310") == "proxy.example:10310"
    assert exit_of(None) == ""


async def test_transport_timeout_is_domain_timeout_without_retry(tmp_path):
    from curl_cffi.requests.exceptions import Timeout

    class Session:
        calls = 0

        async def request(self, *args, **kwargs):
            self.calls += 1
            raise Timeout("deadline", code=28)

    net = Net(tmp_path, limiter=Limiter(None, default=Rule(interval=0, jitter=0)), cache=RawCache(None))
    session = Session()
    net._session = session
    with pytest.raises(TimeoutError):
        await net.request("src", "GET", "https://example.test")
    assert session.calls == 1 and net.counts["src"] == 1


async def test_a_second_line_of_a_source_has_its_own_spacing_and_shares_its_fate(tmp_path):
    from travelops.net.client import Blocked
    from travelops.net.limiter import Limiter, Quarantined, Rule

    slept = []

    async def sleep(seconds):
        slept.append(round(seconds))

    now = [1000.0]
    limiter = Limiter(
        None,
        {"site": Rule(interval=30, jitter=0), "site/handshake": Rule(interval=1, jitter=0)},
        clock=lambda: now[0],
        sleep=sleep,
    )
    net = Net(tmp_path, limiter=limiter)
    answers = iter([Response(200, b"ok"), Response(200, b"ok"), Response(200, b"ok"), Response(429, b"slow down")])

    async def send(method, url, **kw):
        return next(answers)

    net._send = send
    await net.request("site", "POST", "https://site.example/mcp", queue="handshake")
    await net.request("site", "POST", "https://site.example/mcp", queue="handshake")
    await net.request("site", "POST", "https://site.example/mcp")
    assert slept == [1], "the second handshake waited a second; the search did not wait for the handshakes"
    assert net.counts["site"] == 3, "every request is counted for the source, whichever line it took"
    with pytest.raises(Blocked):
        await net.request("site", "POST", "https://site.example/mcp", queue="handshake")
    with pytest.raises(Quarantined):
        await net.request("site", "POST", "https://site.example/mcp")
    with pytest.raises(Quarantined):
        await net.request("site", "POST", "https://site.example/mcp", queue="handshake")
