"""Money and conversion. Prices keep their original currency; conversion is only for comparing and sorting."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class UnknownCurrency(KeyError):
    pass


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", Decimal(str(self.amount)))
        object.__setattr__(self, "currency", self.currency.upper())

    def __str__(self) -> str:
        whole = f"{self.amount.quantize(Decimal('1')):,}".replace(",", " ")
        return f"{whole} {self.currency}"


class Rates:
    """Rates as `units of currency per one unit of base`, the way rate feeds publish them."""

    def __init__(self, base: str, per_base: dict[str, object], day: str) -> None:
        self.base = base.upper()
        self.day = day
        self.per_base = {k.upper(): Decimal(str(v)) for k, v in per_base.items()}
        self.per_base[self.base] = Decimal(1)

    def convert(self, money: Money, to: str) -> Money:
        to = to.upper()
        if money.currency == to:
            return money
        try:
            in_base = money.amount / self.per_base[money.currency]
            return Money((in_base * self.per_base[to]).quantize(Decimal("0.01")), to)
        except KeyError as exc:
            raise UnknownCurrency(exc.args[0]) from None


def parse_open_er_api(payload: dict) -> Rates:
    if payload.get("result") != "success":
        raise ValueError(f"rates feed answered {payload.get('result')!r}")
    return Rates(payload["base_code"], payload["rates"], day=payload["time_last_update_utc"])
