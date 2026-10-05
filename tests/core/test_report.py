from travelops.core.report import SourceReport, Status, combine


def test_combine_keeps_success_and_lists_failed_dates():
    a = SourceReport("tutu", Status.OK, offers=10, requests=3, seconds=2.0)
    b = SourceReport("tutu", Status.TIMEOUT, reason="no answer in 120 s", notes=["2026-11-16"], requests=1)
    merged = combine([a, b])
    assert merged.status is Status.OK and merged.offers == 10 and merged.requests == 4
    assert "2026-11-16: no answer in 120 s" in merged.notes


def test_combine_all_failed_keeps_first_reason():
    a = SourceReport("x", Status.BLOCKED, reason="anti-bot did not let us in")
    b = SourceReport("x", Status.BLOCKED, reason="anti-bot did not let us in")
    assert combine([a, b]).reason == "anti-bot did not let us in"
