"""Registry. Each source module registers itself here when it is added (Tasks 16–22)."""

FLIGHT_SOURCES: dict[str, type] = {}
STAY_SOURCES: dict[str, type] = {}

from ..net.limiter import Rule

RULES: dict[str, Rule] = {
    "airbnb": Rule(interval=10, jitter=3, window=12, per=600, quarantine=1800),
    "booking": Rule(interval=10, jitter=3, window=12, per=600, quarantine=86400),
    "wildberries": Rule(interval=8, jitter=2.4, window=20, per=600, quarantine=1800),
    "kupibilet": Rule(interval=5, jitter=1.5, window=30, per=600, quarantine=1800),
    "onetwotrip": Rule(interval=10, jitter=3, window=15, per=600, quarantine=1800),
    "tutu": Rule(interval=30, jitter=10.5, window=12, per=600, quarantine=1800),
    "aviasales": Rule(interval=12, jitter=3.6, window=40, per=600, quarantine=1800),
    # Public MCP servers made for agents.
    "kiwi": Rule(interval=5, jitter=1.5, window=30, per=600, quarantine=1800),
    "trivago": Rule(interval=3, jitter=1, window=30, per=600, quarantine=1800),
    # Photo CDNs, not the travel sites themselves.
    "images": Rule(interval=0.3, jitter=0.2, window=120, per=60, quarantine=600),
}

from .flights.aviasales import Source as Aviasales

FLIGHT_SOURCES["aviasales"] = Aviasales

from .flights.tutu import Source as Tutu

FLIGHT_SOURCES["tutu"] = Tutu

from .flights.onetwotrip import Source as OneTwoTrip

FLIGHT_SOURCES["onetwotrip"] = OneTwoTrip

from .flights.kupibilet import Source as Kupibilet

FLIGHT_SOURCES["kupibilet"] = Kupibilet

from .flights.wildberries import Source as Wildberries

FLIGHT_SOURCES["wildberries"] = Wildberries

from .flights.kiwi import Source as Kiwi

FLIGHT_SOURCES["kiwi"] = Kiwi

from .stays.booking import Source as Booking

STAY_SOURCES["booking"] = Booking

from .stays.airbnb import Source as Airbnb

STAY_SOURCES["airbnb"] = Airbnb

from .stays.trivago import Source as Trivago

STAY_SOURCES["trivago"] = Trivago
