import pytest

from travelops.net.limiter import Limiter, Quarantined, Rule


class FakeTime:
    def __init__(self):
        self.now, self.slept = 1000.0, []

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


def make(tmp_path, t, **rule):
    return Limiter(tmp_path / "l.sqlite", {"x": Rule(**rule)}, clock=t.clock, sleep=t.sleep, rand=lambda: 0.0)


async def test_spaces_requests(tmp_path):
    t = FakeTime()
    lim = make(tmp_path, t, interval=5, jitter=0)
    await lim.acquire("x")
    await lim.acquire("x")
    assert t.slept == [5]


async def test_window(tmp_path):
    t = FakeTime()
    lim = make(tmp_path, t, interval=0, jitter=0, window=2, per=60)
    for _ in range(3):
        await lim.acquire("x")
    assert t.slept == [60]


async def test_block_quarantines_and_survives_restart(tmp_path):
    t = FakeTime()
    lim = make(tmp_path, t, interval=1, jitter=0, quarantine=600)
    await lim.acquire("x")
    lim.blocked("x")
    again = make(tmp_path, t, interval=1, jitter=0, quarantine=600)
    with pytest.raises(Quarantined):
        await again.acquire("x")
    t.now += 601
    await again.acquire("x")
    assert t.slept == [], "a slowed bucket after quarantine still waits nothing extra once time passed"


async def test_buckets_are_independent(tmp_path):
    t = FakeTime()
    lim = make(tmp_path, t, interval=5, jitter=0)
    await lim.acquire("x@home:1080")
    await lim.acquire("x@vps")
    assert t.slept == []


async def test_reset(tmp_path):
    t = FakeTime()
    lim = make(tmp_path, t, interval=1, jitter=0)
    lim.blocked("x")
    lim.reset("x")
    await lim.acquire("x")


def test_bucket_name():
    assert Limiter.bucket("booking", "") == "booking"
    assert Limiter.bucket("booking", "home:1080") == "booking@home:1080"
