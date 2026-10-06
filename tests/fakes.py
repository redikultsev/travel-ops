"""Searches that send nothing: every kind searches with a function of the test and rates come from a table."""

from dataclasses import replace

from travelops.core.money import Rates
from travelops.kinds import KINDS
from travelops.search import FlightSearch, GroundSearch, StaySearch
from travelops.searches import Searches


async def no_flights(query, sources, ctx, rates, currency, **kwargs):
    return FlightSearch(query, currency, [], [])


async def no_stays(query, sources, ctx, rates, currency, **kwargs):
    return StaySearch(query, currency, [], [])


async def no_rides(query, sources, ctx, rates, currency, **kwargs):
    return GroundSearch(query, currency, [], [])


def fake_searches(
    app,
    *,
    flights=no_flights,
    stays=no_stays,
    ground=no_rides,
    estimate=lambda *args: 0.0,
    rates=Rates("EUR", {"USD": 1.2}, "offline-test"),
    load=None,
) -> Searches:
    """`estimate` is the wait every search would cost; `load` replaces loading the rates altogether. No kind looks
    anything up before its search, so a stay has no centre to measure from."""

    async def table(net):
        return rates

    searched = {"flights": flights, "stays": stays, "ground": ground}
    kinds = {
        name: replace(kind, search=searched[name], estimate=estimate, context=None) for name, kind in KINDS.items()
    }
    return Searches(app, kinds, load or table)
