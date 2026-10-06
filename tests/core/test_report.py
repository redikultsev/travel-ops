from travelops.core.report import SourceReport, Status, combine


def test_combine_keeps_success_and_lists_failed_dates():
    a = SourceReport("tutu", Status.OK, offers=10, requests=3, seconds=2.0)
    b = SourceReport("tutu", Status.TIMEOUT, reason="no answer in 120 s", notes=["2026-11-16"], requests=1)
    merged = combine([a, b])
    assert merged.status is Status.OK and merged.offers == 10 and merged.requests == 4
    assert "failed for 2026-11-16: no answer in 120 s" in merged.notes


def test_combine_all_failed_keeps_first_reason():
    a = SourceReport("x", Status.BLOCKED, reason="anti-bot did not let us in")
    b = SourceReport("x", Status.BLOCKED, reason="anti-bot did not let us in")
    assert combine([a, b]).reason == "anti-bot did not let us in"


def test_the_brief_says_who_answered_who_did_not_and_where_the_answer_is_narrow():
    from travelops.core.report import brief

    reports = [
        {
            "source": "tutu",
            "status": "ok",
            "reason": "",
            "notes": ["BEG-TIV: results truncated: 30 of 62 itineraries", "resolved from: BEG to Белград"],
        },
        {
            "source": "onetwotrip",
            "status": "ok",
            "reason": "",
            "notes": ["requested cabin: economy", "failed for BEG-TGD: a request got no answer in 30 s"],
        },
        {"source": "kupibilet", "status": "empty", "reason": "", "notes": []},
        {"source": "booking", "status": "blocked", "reason": "Booking WAF refused the address", "notes": []},
    ]
    assert brief(reports) == {
        "ok": ["tutu", "onetwotrip"],
        "empty": ["kupibilet"],
        "problems": [{"source": "booking", "status": "blocked", "reason": "Booking WAF refused the address"}],
        "limits": [
            "tutu: BEG-TIV: results truncated: 30 of 62 itineraries",
            "onetwotrip: failed for BEG-TGD: a request got no answer in 30 s",
        ],
    }


def test_a_limit_repeated_for_every_date_is_said_once():
    from travelops.core.report import brief

    notes = [
        f"{day} BEG-TIV: results truncated: 30 of {n} itineraries"
        for day, n in (("2026-10-21", 64), ("2026-10-22", 57))
    ]
    notes += ["2026-10-21 BEG-TGD: results truncated: 30 of 112 itineraries", "resolved from: BEG to Белград"]
    out = brief([{"source": "tutu", "status": "ok", "reason": "", "notes": notes}])
    assert out["limits"] == [
        "tutu: 2026-10-21 BEG-TIV: results truncated: 30 of 64 itineraries (and 2 more dates or routes like it)"
    ]


def test_a_refusal_repeated_for_every_route_is_said_once():
    from travelops.core.report import brief

    notes = [f"failed for BEG-{code}: party pricing is verified only for one adult" for code in ("LIS", "CAT")]
    out = brief([{"source": "kupibilet", "status": "not_configured", "reason": "x", "notes": notes}])
    assert out["limits"] == [
        "kupibilet: failed for BEG-LIS: party pricing is verified only for one adult (and 1 more dates or routes like it)"
    ]
