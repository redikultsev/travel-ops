"""Registry. Each source module registers itself here when it is added (Tasks 16–22)."""

FLIGHT_SOURCES: dict[str, type] = {}
STAY_SOURCES: dict[str, type] = {}
GROUND_SOURCES: dict[str, type] = {}

from ..net.limiter import Rule

RULES: dict[str, Rule] = {
    "airbnb": Rule(interval=10, jitter=3, window=12, per=600, quarantine=1800),
    "booking": Rule(interval=10, jitter=3, window=12, per=600, quarantine=86400),
    "wildberries": Rule(interval=8, jitter=2.4, window=20, per=600, quarantine=1800),
    "kupibilet": Rule(interval=5, jitter=1.5, window=30, per=600, quarantine=1800),
    "onetwotrip": Rule(interval=10, jitter=3, window=15, per=600, quarantine=1800),
    # Tutu publishes no limit. Searches go as often as to Aviasales; the two handshake messages of its MCP
    # session are not searches and wait in a line of their own.
    "tutu": Rule(interval=12, jitter=3.6, window=12, per=600, quarantine=1800),
    "tutu/handshake": Rule(interval=1, jitter=0.5, window=12, per=600, quarantine=1800),
    "aviasales": Rule(interval=12, jitter=3.6, window=40, per=600, quarantine=1800),
    # Public MCP servers made for agents.
    "kiwi": Rule(interval=5, jitter=1.5, window=30, per=600, quarantine=1800),
    "trivago": Rule(interval=3, jitter=1, window=30, per=600, quarantine=1800),
    "12go": Rule(interval=5, jitter=1.5, window=30, per=600, quarantine=1800),
    "12go/handshake": Rule(interval=1, jitter=0.5, window=30, per=600, quarantine=1800),
    # FlixBus's own shop JSON; city lookups are cached and wait in a short line of their own.
    "flixbus": Rule(interval=5, jitter=1.5, window=30, per=600, quarantine=1800),
    "flixbus/lookup": Rule(interval=1, jitter=0.5, window=30, per=600, quarantine=1800),
    # Trip.com: each search is a browser opening the results page, so few and far apart.
    "tripcom": Rule(interval=20, jitter=6, window=10, per=600, quarantine=3600),
    # Google's search page, read without an API: slow and few, and a refusal rests it for an hour.
    "google": Rule(interval=10, jitter=3, window=20, per=600, quarantine=3600),
    # A public routing service that asks for one request a second at most.
    "routing": Rule(interval=1.1, jitter=0.3, window=30, per=600, quarantine=3600),
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

from .flights.google import Source as Google

FLIGHT_SOURCES["google"] = Google

from .flights.tripcom import Source as TripCom

FLIGHT_SOURCES["tripcom"] = TripCom

from .stays.booking import Source as Booking

STAY_SOURCES["booking"] = Booking

from .stays.airbnb import Source as Airbnb

STAY_SOURCES["airbnb"] = Airbnb

from .stays.trivago import Source as Trivago

STAY_SOURCES["trivago"] = Trivago

from .ground.tutu import Source as TutuGround

GROUND_SOURCES["tutu"] = TutuGround

from .ground.twelvego import Source as TwelveGo

GROUND_SOURCES["12go"] = TwelveGo

from .ground.flixbus import Source as FlixBus

GROUND_SOURCES["flixbus"] = FlixBus
