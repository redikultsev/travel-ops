"""Where a place is and which airports serve it. The agent must not guess an airport: a town such as Kotor has
none of its own, and the right ones (Tivat, Podgorica, Dubrovnik) follow from distance, not from the name."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import airportsdata

from .net.client import Net

GEOCODER = "https://geocoding-api.open-meteo.com/v1/search"
# FOSSGIS's public OSRM (https://routing.openstreetmap.de/about.html): one request a second at most, a real user
# agent, attribution. One request per trip, kept for a month, is well inside that.
ROUTING = "https://routing.openstreetmap.de/routed-car/table/v1/driving/"
AGENT = "travel-ops/0.1 (+https://github.com/redikultsev/travel-ops)"
ROAD_CREDIT = "road distances: OSRM on OpenStreetMap data, routing.openstreetmap.de; © OpenStreetMap contributors"
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


async def locate(net: Net, name: str, country: str | None = None, language: str = "en") -> list[Place]:
    """Candidates for a place name, the geocoder's best first. Names in Latin script; a country narrows them.
    `language` is the language of the names returned."""
    query = name.split(",")[0].strip()
    if country is None and "," in name:
        country = name.split(",", 1)[1].strip() or None
    response = await net.request(
        "geocoder",
        "GET",
        GEOCODER,
        cache_ttl=30 * 86400,
        params={"name": query, "count": 10, "language": language, "format": "json"},
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


async def with_roads(
    net: Net, lat: float, lon: float, airports: list[dict], at_most: int = 4
) -> tuple[list[dict], str]:
    """Add the drive from each of the nearest airports to the place: `km_road` and `minutes_road`, by car, without
    traffic, waiting at a border or a ferry. A routing service that does not answer costs these fields, nothing
    else: the airports keep their straight-line distance. Returns the airports and a note on what was done."""
    near = [a for a in airports[:at_most] if a["iata"] in _AIRPORTS]
    if not near:
        return airports, ""
    points = ";".join(
        f"{lon:.5f},{lat:.5f}"
        for lon, lat in [(lon, lat)] + [(_AIRPORTS[a["iata"]]["lon"], _AIRPORTS[a["iata"]]["lat"]) for a in near]
    )
    try:
        response = await net.request(
            "routing",
            "GET",
            ROUTING + points,
            params={
                "sources": ";".join(str(i) for i in range(1, len(near) + 1)),
                "destinations": "0",
                "annotations": "duration,distance",
            },
            headers={"user-agent": AGENT},
            cache_ttl=30 * 86400,
            timeout=20,
        )
        table = response.json()
        if table.get("code") != "Ok":
            raise ValueError(table.get("code"))
        for airport, km, seconds in zip(near, table["distances"], table["durations"]):
            if km[0] is not None and seconds[0] is not None:
                airport["km_road"] = round(km[0] / 1000)
                airport["minutes_road"] = round(seconds[0] / 60)
    except Exception as exc:  # the drive is a courtesy: never fail a trip over it
        return airports, f"road distances unavailable ({type(exc).__name__}); km_straight is a straight line"
    return airports, ROAD_CREDIT
