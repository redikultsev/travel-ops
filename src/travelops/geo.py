"""Where a place is and which airports serve it. The agent must not guess an airport: a town such as Kotor has
none of its own, and the right ones (Tivat, Podgorica, Dubrovnik) follow from distance, not from the name."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import airportsdata

from .net.client import Net

GEOCODER = "https://geocoding-api.open-meteo.com/v1/search"
# The geocoder's point for a town is GeoNames', and GeoNames puts some far from what a visitor calls the centre:
# Sarajevo 6 km west of the old town. OpenStreetMap's place node is put, by its own convention, on the central
# square or the town hall; Wikidata's coordinate (P625) follows Wikipedia's and is often rounded. So the centre is
# the place node whose `wikidata` tag is the town's item, else the item's coordinate, else the geocoder's point
# (docs/geo.md). The item is found by the GeoNames id the geocoder returns (P1566): no name is guessed.
WIKIDATA = "https://www.wikidata.org/w/api.php"
# OSMF's Nominatim (https://operations.osmfoundation.org/policies/nominatim/): one request a second at most, an
# identifying user agent, results cached, attribution. One request per town, kept for a month.
NOMINATIM = "https://nominatim.openstreetmap.org/search"
CENTRE_CREDIT = "centre of the place: OpenStreetMap place node via Nominatim; © OpenStreetMap contributors"
# A point farther than this from the geocoder's belongs to another place, not to a better centre of this one.
CENTRE_DRIFT_KM = 25
# FOSSGIS's public OSRM (https://routing.openstreetmap.de/about.html): one request a second at most, a real user
# agent, attribution. One request per trip, kept for a month, is well inside that.
ROUTING = "https://routing.openstreetmap.de/routed-car/table/v1/driving/"
AGENT = "travel-ops/0.1 (+https://github.com/redikultsev/travel-ops)"
ROAD_CREDIT = "road distances: OSRM on OpenStreetMap data, routing.openstreetmap.de; © OpenStreetMap contributors"
_AIRPORTS = airportsdata.load("IATA")
# Airports airlines fly to, from OurAirports (scripts/update_airports.py): the table above also holds closed
# airports and air bases, which no search answers for.
SCHEDULED = frozenset(
    line.strip()
    for line in (Path(__file__).parent / "data" / "scheduled_airports.txt").read_text().splitlines()
    if line.strip() and not line.startswith("#")
)
FORMAL = re.compile(r"^(the |(republic|kingdom|state|commonwealth|principality|grand duchy) of (the )?)", re.I)


@dataclass(frozen=True)
class Place:
    name: str
    country: str
    country_code: str
    region: str | None
    lat: float
    lon: float
    geonames_id: int | None = field(default=None, compare=False)  # the geocoder's id, which is GeoNames'

    def label(self) -> str:
        """The place as a site's search box reads it: "Istanbul, Türkiye". The geocoder writes some countries in
        their formal style ("Republic of Türkiye"), which Booking does not recognise and answers with its home
        page."""
        return f"{self.name}, {FORMAL.sub('', self.country)}"


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    h = (
        0.5
        - math.cos((lat2 - lat1) * p) / 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    )
    return 12742 * math.asin(math.sqrt(h))


# English names people write that differ from the geocoder's own, by ISO code.
COUNTRY_NAMES = {
    "turkey": "TR",
    "turkiye": "TR",
    "czechia": "CZ",
    "czech republic": "CZ",
    "russia": "RU",
    "usa": "US",
    "united states": "US",
    "united kingdom": "GB",
    "united states of america": "US",
    "america": "US",
    "uk": "GB",
    "great britain": "GB",
    "britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "south korea": "KR",
    "korea": "KR",
    "holland": "NL",
    "the netherlands": "NL",
    "macedonia": "MK",
    "bosnia": "BA",
    "ivory coast": "CI",
    "uae": "AE",
    "emirates": "AE",
    "vatican": "VA",
    "burma": "MM",
    "cape verde": "CV",
    "swaziland": "SZ",
    "east timor": "TL",
    "laos": "LA",
    "moldova": "MD",
    "iran": "IR",
    "syria": "SY",
    "vietnam": "VN",
}


def _folded(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c)).strip()


def in_country(place: Place, country: str) -> bool:
    """Whether a place is in the country a person named: its code, its name as the geocoder writes it or a word
    of it ("Türkiye" in "Republic of Türkiye"), or a common English name ("Turkey")."""
    wanted = _folded(country)
    code = place.country_code.lower()
    if code in (wanted, COUNTRY_NAMES.get(wanted, "").lower()):
        return True
    name = _folded(place.country)
    return wanted == name or (len(wanted) > 3 and (wanted in name.split() or wanted in name or name in wanted))


def same_country(asked: str, named: str) -> bool:
    """Whether a country a person wrote and one a site names are one: "Turkey" and "Türkiye", "UK" and "United
    Kingdom"."""
    a, b = _folded(asked), _folded(FORMAL.sub("", named))
    if not a or not b:
        return False
    if a == b or (len(a) > 3 and (a in b or b in a)):
        return True
    return bool(COUNTRY_NAMES.get(a)) and COUNTRY_NAMES.get(a) == COUNTRY_NAMES.get(b)


def parse_places(payload: dict, country: str | None = None) -> list[Place]:
    places = [
        Place(
            r["name"],
            r.get("country") or "",
            (r.get("country_code") or "").upper(),
            r.get("admin1"),
            float(r["latitude"]),
            float(r["longitude"]),
            r.get("id"),
        )
        for r in payload.get("results") or []
    ]
    if country:
        places = [p for p in places if in_country(p, country)]
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
    """Airports with scheduled passenger flights, by distance. Which routes they have is not known here: a search
    tells."""
    found = []
    for code, airport in _AIRPORTS.items():
        if code not in SCHEDULED:
            continue
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
    found = asdict(place)
    del found["geonames_id"]
    return found


def parse_item(payload: dict, place: Place) -> tuple[str, tuple[float, float] | None] | None:
    """The one Wikidata item with this GeoNames id: its id, and its primary coordinate on Earth when it has one
    near the place. Several items with one id answer nothing rather than a guess."""
    pages = (payload.get("query") or {}).get("pages") or []
    if len(pages) != 1 or not str(pages[0].get("title", "")).startswith("Q"):
        return None
    points = [
        (float(c["lat"]), float(c["lon"]))
        for c in pages[0].get("coordinates") or []
        if c.get("primary") and c.get("globe", "earth") == "earth"
    ]
    near = [p for p in points if distance_km(place.lat, place.lon, *p) <= CENTRE_DRIFT_KM]
    return pages[0]["title"], near[0] if len(points) == 1 and near else None


def parse_place_node(payload: list, item: str, place: Place) -> tuple[float, float] | None:
    """The point of OpenStreetMap's place node for this Wikidata item, from Nominatim's answer. A boundary stands
    for its node only when Nominatim linked one (`linked_place`): else its point is the middle of its area."""
    for hit in payload if isinstance(payload, list) else []:
        tags = hit.get("extratags") or {}
        if tags.get("wikidata") != item or not (hit.get("category") == "place" or tags.get("linked_place")):
            continue
        point = (float(hit["lat"]), float(hit["lon"]))
        if distance_km(place.lat, place.lon, *point) <= CENTRE_DRIFT_KM:
            return point
    return None


def _answered(response) -> bool:
    return response.status == 200 and b'"error"' not in response.body[:200]


async def centre(net: Net, place: Place) -> dict:
    """The centre of a place, to measure stays from, and `from` which source: `openstreetmap`, `wikidata`, or
    `geonames` (the geocoder's own point). Up to two requests, one after the other, each kept for a month; a
    failure costs only the better point."""
    here = {"name": place.label(), "lat": place.lat, "lon": place.lon, "from": "geonames"}
    if place.geonames_id is None:
        return here
    month = 30 * 86400
    try:
        response = await net.request(
            "wikidata",
            "GET",
            WIKIDATA,
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": f"haswbstatement:P1566={place.geonames_id}",
                "gsrlimit": 2,
                "prop": "coordinates",
                "coprimary": "primary",
                "format": "json",
                "formatversion": 2,
            },
            headers={"user-agent": AGENT},
            cache_ttl=month,
            reuse_if=_answered,
            timeout=15,
        )
        found = parse_item(response.json(), place) if _answered(response) else None
    except Exception:  # the better point is a courtesy: the geocoder's still measures
        return here
    if found is None:
        return here
    item, point = found
    if point is not None:
        here = {**here, "lat": point[0], "lon": point[1], "from": "wikidata"}
    try:
        response = await net.request(
            "nominatim",
            "GET",
            NOMINATIM,
            params={
                "q": place.name,
                "countrycodes": place.country_code.lower(),
                "format": "jsonv2",
                "limit": 5,
                "extratags": 1,
            },
            headers={"user-agent": AGENT},
            cache_ttl=month,
            reuse_if=lambda r: r.status == 200,
            timeout=15,
        )
        node = parse_place_node(response.json(), item, place) if response.status == 200 else None
    except Exception:
        return here
    if node is None:
        return here
    return {**here, "lat": node[0], "lon": node[1], "from": "openstreetmap", "credit": CENTRE_CREDIT}


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
