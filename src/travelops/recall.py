"""Search, or remember. The same question asked again within half an hour is answered from memory: a follow-up
then costs no request and spends none of a source's patience. `refresh` always searches."""

from __future__ import annotations

from dataclasses import asdict

from .memory import Stored
from .search import search_flights, search_stays
from .serialize import flight_search_json, stay_search_json


def _key(app, kind: str, query, sources: list, currency: str) -> str:
    return app.results.key(kind, asdict(query), [s.name for s in sources], currency)


def remembered(app, kind: str, query, sources: list, currency: str) -> Stored | None:
    return app.results.recent(_key(app, kind, query, sources, currency))


def _note(result: dict, selection) -> dict:
    if selection is not None:
        for report in result["sources"]:
            report["notes"].append("source selection: " + ", ".join(selection))
    return result


async def flights(app, query, sources: list, rates, currency: str, *, refresh=False, selection=None) -> Stored:
    stored = None if refresh else remembered(app, "flights", query, sources, currency)
    if stored is None:
        result = flight_search_json(await search_flights(query, sources, app.ctx, rates, currency), rates)
        stored = app.results.put("flights", _key(app, "flights", query, sources, currency), _note(result, selection))
    return stored


async def stays(app, query, sources: list, rates, currency: str, *, refresh=False, selection=None) -> Stored:
    stored = None if refresh else remembered(app, "stays", query, sources, currency)
    if stored is None:
        result = stay_search_json(await search_stays(query, sources, app.ctx, rates, currency), rates)
        stored = app.results.put("stays", _key(app, "stays", query, sources, currency), _note(result, selection))
    return stored
