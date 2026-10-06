"""Validated personal defaults and shared data directory."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import yaml
from .core.flights import CABINS


@dataclass(frozen=True)
class Travellers:
    adults: int = 1
    children: int = 0
    infants: int = 0


@dataclass(frozen=True)
class StaysProfile:
    adults: int = 2
    min_rating: float = 8.0
    must_have: tuple[str, ...] = ()  # checked against a property page, so each one costs a `stay_details` read
    max_center_km: float = 15.0  # a stay farther out is in another town, whatever the site files it under


@dataclass(frozen=True)
class Profile:
    home_airports: tuple[str, ...] = ()  # nobody's home is a sensible default: without a profile the agent asks
    nearby_airports: tuple[str, ...] = ()
    currency: str = "EUR"
    travellers: Travellers = field(default_factory=Travellers)
    cabin: str = "economy"
    baggage: str = "carry_on"
    max_stops: int = 1
    avoid_airlines: tuple[str, ...] = ()
    stays: StaysProfile = field(default_factory=StaysProfile)
    confirm_over_seconds: int = 300  # a search estimated longer than this asks the human first
    max_leg_hours: int = 24  # door to door one way; a connection that waits two days is not an option


def data_dir(root: Path) -> Path:
    value = os.environ.get("TRAVELOPS_DATA")
    return Path(value).expanduser().resolve() if value else Path(root) / "data"


def _count(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _strings(value, label):
    if not isinstance(value, (list, tuple)) or not all(isinstance(x, str) and x for x in value):
        raise ValueError(f"{label} must be a list of nonempty strings")
    return tuple(value)


def _distance(value) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError("stays.max_center_km must be a positive number")
    return float(value)


def load_profile(root: Path) -> Profile:
    path = Path(root) / "profile.yml"
    try:
        values = yaml.safe_load(path.read_text()) if path.exists() else {}
    except yaml.YAMLError as exc:
        raise ValueError(f"profile.yml is not valid YAML: {exc}") from exc
    values = values or {}
    if not isinstance(values, dict):
        raise ValueError("profile.yml must contain a mapping")
    defaults = Profile()
    unknown = set(values) - set(Profile.__dataclass_fields__)
    if unknown:
        raise ValueError(f"unknown profile fields: {', '.join(sorted(unknown))}")
    bag = values.get("baggage", defaults.baggage)
    if bag not in ("carry_on", "checked"):
        raise ValueError("baggage must be carry_on or checked")
    cabin = values.get("cabin", defaults.cabin)
    if cabin not in CABINS:
        raise ValueError(f"cabin must be one of {', '.join(CABINS)}")
    currency = values.get("currency", defaults.currency)
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
        raise ValueError("currency must be a three-letter code")
    traveller = values.get("travellers", {})
    stays = values.get("stays", {})
    if not isinstance(traveller, dict) or not isinstance(stays, dict):
        raise ValueError("travellers and stays must be mappings")
    if set(traveller) - {"adults", "children", "infants"} or set(stays) - {
        "adults",
        "min_rating",
        "must_have",
        "max_center_km",
    }:
        raise ValueError("unknown travellers or stays fields")
    party = Travellers(
        _count(traveller.get("adults", 1), "travellers.adults", 1),
        _count(traveller.get("children", 0), "travellers.children"),
        _count(traveller.get("infants", 0), "travellers.infants"),
    )
    if party.infants > party.adults:
        raise ValueError("travellers.infants cannot exceed adults")
    score = stays.get("min_rating", 8.0)
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 10:
        raise ValueError("stays.min_rating must be between 0 and 10")
    home = _strings(values.get("home_airports", defaults.home_airports), "home_airports")
    nearby = _strings(values.get("nearby_airports", defaults.nearby_airports), "nearby_airports")
    if any(len(x) != 3 or not x.isalpha() for x in home + nearby):
        raise ValueError("airport lists must contain three-letter IATA codes")
    return Profile(
        tuple(x.upper() for x in home),
        tuple(x.upper() for x in nearby),
        currency.upper(),
        party,
        cabin,
        bag,
        _count(values.get("max_stops", 1), "max_stops"),
        _strings(values.get("avoid_airlines", ()), "avoid_airlines"),
        StaysProfile(
            _count(stays.get("adults", 2), "stays.adults", 1),
            float(score),
            _strings(stays.get("must_have", ()), "stays.must_have"),
            _distance(stays.get("max_center_km", 15.0)),
        ),
        _count(values.get("confirm_over_seconds", defaults.confirm_over_seconds), "confirm_over_seconds"),
        _count(values.get("max_leg_hours", defaults.max_leg_hours), "max_leg_hours", 1),
    )
