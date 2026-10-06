"""Whose request it was. Runs of one source overlap (one per route and date), and a counter kept per source
cannot tell them apart: each run would report the requests of its neighbours as its own."""

from __future__ import annotations

from collections import Counter
from contextvars import ContextVar

# Set by a run for its own task; everything the run awaits, or starts, counts into it.
RUN: ContextVar[Counter | None] = ContextVar("travelops_run", default=None)


def count(totals: Counter | None, source: str) -> None:
    if totals is not None:
        totals[source] += 1
    run = RUN.get()
    if run is not None:
        run[source] += 1
