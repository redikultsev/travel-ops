"""Where a place is and which airports serve it. The agent must not guess an airport: a town such as Kotor has
none of its own, and the right ones (Tivat, Podgorica, Dubrovnik) follow from distance, not from the name."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import airportsdata

from .net.client import Net

GEOCODER = "https://geocoding-api.open-meteo.com/v1/search"
_AIRPORTS = airportsdata.load("IATA")


@dataclass(frozen=True)
class Place:
    name: str
    country: str
    country_code: str
    region: str | None
    lat: float
    lon: float

    def label(self) -> str:
        return f"{self.name}, {self.country}"


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    h = (
        0.5
        - math.cos((lat2 - lat1) * p) / 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    )
    return 12742 * math.asin(math.sqrt(h))


def parse_places(payload: dict, country: str | None = None) -> list[Place]:
    places = [
        Place(
            r["name"],
            r.get("country") or "",
            (r.get("country_code") or "").upper(),
            r.get("admin1"),
            float(r["latitude"]),
            float(r["longitude"]),
        )
        for r in payload.get("results") or []
    ]
    if country:
        wanted = country.strip().lower()
        places = [p for p in places if wanted in (p.country.lower(), p.country_code.lower())]
    return places


async def locate(net: Net, name: str, country: str | None = None) -> list[Place]:
    """Candidates for a place name, the geocoder's best first. Names in Latin script; a country narrows them."""
    query = name.split(",")[0].strip()
    if country is None and "," in name:
        country = name.split(",", 1)[1].strip() or None
    response = await net.request(
        "geocoder",
        "GET",
        GEOCODER,
        cache_ttl=30 * 86400,
        params={"name": query, "count": 10, "language": "en", "format": "json"},
    )
    return parse_places(response.json(), country)


def airports_near(lat: float, lon: float, radius_km: float = 100, limit: int = 6) -> list[dict]:
    """Airports with an IATA code by distance. Whether airlines fly there is not known here: a search tells."""
    found = []
    for code, airport in _AIRPORTS.items():
        km = distance_km(lat, lon, airport["lat"], airport["lon"])
        if km <= radius_km:
            found.append(
                {
                    "iata": code,
                    "name": airport["name"],
                    "city": airport["city"],
                    "country_code": airport["country"],
                    "km_straight": round(km),
                }
            )
    return sorted(found, key=lambda a: a["km_straight"])[:limit]


def place_json(place: Place) -> dict:
    return asdict(place)
