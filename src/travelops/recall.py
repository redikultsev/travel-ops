"""Search, or remember. The same question asked again within half an hour is answered from memory: a follow-up
then costs no request and spends none of a source's patience. `refresh` always searches."""

from __future__ import annotations

from dataclasses import asdict

from .memory import REUSE_SECONDS, Stored
from .search import search_flights, search_stays
from .serialize import flight_search_json, stay_search_json


def _key(app, kind: str, query, sources: list, currency: str) -> str:
    # Empty optional fields are left out, so a query written before a field existed still finds its answer.
    asked = {name: value for name, value in asdict(query).items() if value != ()}
    return app.results.key(kind, asked, [s.name for s in sources], currency)


def remembered(app, kind: str, query, sources: list, currency: str) -> Stored | None:
    key = _key(app, kind, query, sources, currency)
    # A recording has no age: it is the only answer a replay can give.
    if app.replay:
        return app.results.recent(key, None, any_sources=True)
    stored = app.results.recent(key)
    # A search that brought nothing because its sources failed is not an answer worth repeating: ask again.
    if (
        stored
        and not stored.result["cards"]
        and any(r["status"] not in ("ok", "empty") for r in stored.result["sources"])
    ):
        return None
    return stored


def _note(result: dict, selection) -> dict:
    if selection is not None:
        for report in result["sources"]:
            report["notes"].append("source selection: " + ", ".join(selection))
    return result


async def _recall(
    app, kind, search, to_json, query, sources, rates, currency, refresh, selection, extra=None, **options
) -> Stored:
    stored = None if refresh and not app.replay else remembered(app, kind, query, sources, currency)
    if stored is not None:
        if app.replay:
            # The recording stands for the search that was asked for. Asked again when the first is too old to
            # reuse, or with `refresh`, it is a search made now.
            stored.fresh = True
            if refresh or app.results.clock() - stored.at > REUSE_SECONDS:
                stored.at = app.results.clock()
        return stored
    if app.replay:
        raise ValueError(f"the replay has no {kind} search for {asdict(query)}")
    result = to_json(await search(query, sources, app.ctx, rates, currency, **options), rates)
    result.update(extra or {})
    return app.results.put(kind, _key(app, kind, query, sources, currency), _note(result, selection))


async def flights(
    app, query, sources: list, rates, currency: str, *, refresh=False, selection=None, sharing: int = 1
) -> Stored:
    """`sharing`: how many flight searches run at once over these sources (see `search_flights`)."""
    return await _recall(
        app,
        "flights",
        search_flights,
        flight_search_json,
        query,
        sources,
        rates,
        currency,
        refresh,
        selection,
        sharing=sharing,
    )


async def stays(
    app, query, sources: list, rates, currency: str, *, refresh=False, selection=None, center=None
) -> Stored:
    """`center` is where the place is: stays that give coordinates but no distance are measured from it."""
    return await _recall(
        app,
        "stays",
        search_stays,
        stay_search_json,
        query,
        sources,
        rates,
        currency,
        refresh,
        selection,
        {"center": center} if center else None,
    )
