import json

from travelops.scrub import scrub


def test_json_secrets_are_replaced():
    body = json.dumps(
        {"search_token": "abc", "data": {"sessionId": "s", "price": 100, "items": [{"auth": "x", "name": "ok"}]}}
    ).encode()
    out = json.loads(scrub(body))
    assert out["search_token"] == "SCRUBBED" and out["data"]["sessionId"] == "SCRUBBED"
    assert out["data"]["price"] == 100 and out["data"]["items"][0] == {"auth": "SCRUBBED", "name": "ok"}


def test_html_secrets_are_replaced():
    html = b'<script>window.csrfToken="abc123";var x={"api_key":"k1"};</script>'
    out = scrub(html)
    assert b"abc123" not in out and b"k1" not in out


def test_emails_are_removed():
    assert b"a@b.co" not in scrub(b'{"contact": "a@b.co"}')


def test_checkout_references_and_offer_hashes_are_removed():
    body = json.dumps(
        {"checkout_ref": {"offer_hash": "opaque-checkout"}, "variants": [{"offer_hash": "opaque-fare", "price": 100}]}
    ).encode()
    result = json.loads(scrub(body))
    assert result["checkout_ref"] == "SCRUBBED"
    assert result["variants"][0] == {"offer_hash": "SCRUBBED", "price": 100}
