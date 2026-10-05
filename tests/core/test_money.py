from decimal import Decimal

import pytest

from travelops.core.money import Money, Rates, UnknownCurrency, parse_open_er_api


def test_money_normalizes():
    m = Money("1000.5", "rub")
    assert m.amount == Decimal("1000.5") and m.currency == "RUB"


def test_convert_through_base():
    rates = Rates("EUR", {"RUB": "100", "RSD": "117"}, day="2026-10-04")
    assert rates.convert(Money(1000, "RUB"), "RSD") == Money("1170.00", "RSD")


def test_same_currency_is_untouched():
    rates = Rates("EUR", {}, day="d")
    m = Money(5, "EUR")
    assert rates.convert(m, "eur") is m


def test_unknown_currency():
    with pytest.raises(UnknownCurrency):
        Rates("EUR", {}, day="d").convert(Money(1, "XYZ"), "EUR")


def test_parse_feed():
    rates = parse_open_er_api(
        {
            "result": "success",
            "base_code": "EUR",
            "time_last_update_utc": "Sat, 04 Oct 2026 00:02:31 +0000",
            "rates": {"EUR": 1, "RUB": 95.1},
        }
    )
    assert rates.convert(Money("95.1", "RUB"), "EUR") == Money("1.00", "EUR")


def test_rates_day_is_a_plain_date():
    feed = {
        "result": "success",
        "base_code": "EUR",
        "time_last_update_utc": "Mon, 05 Oct 2026 00:02:31 +0000",
        "rates": {"EUR": 1},
    }
    assert parse_open_er_api(feed).day == "2026-10-05"
    assert parse_open_er_api(dict(feed, time_last_update_utc="soon")).day == "soon"
