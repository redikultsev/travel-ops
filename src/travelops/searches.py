"""A search as every caller needs it: the tools, the command line, a whole trip. The same question asked again
within half an hour is answered from memory, so a follow-up costs no request and spends none of a source's
patience; a search that would wait long asks for a confirmation before anything is sent; and what comes back is a
view that starts from the profile's bars and says how old its prices are. `refresh` always searches."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from .kinds import KINDS, Kind
from .memory import REUSE_SECONDS, Stored
from .rates import load_rates


# Fields added after recordings were made: left out of the key while unset, so the recordings still answer.
UNSET_IF_NONE = frozenset({"min_rating", "max_total", "max_night_eur"})


class NeedsConfirmation(Exception):
    """The search would wait longer than the profile allows without asking. Nothing has been sent."""

    def __init__(self, seconds: float):
        super().__init__(f"about {seconds:.0f} seconds of request spacing")
        self.seconds = seconds

    def json(self) -> dict:
        return {
            "needs_confirmation": True,
            "estimate_seconds": round(self.seconds, 1),
            "message": f"This search needs about {self.seconds:.0f} seconds of request spacing, plus response "
            "time. Ask the human to approve before passing confirm=True.",
        }


class Searches:
    """`kinds` and `rates` are where a test puts its own searches and exchange rates."""

    def __init__(self, app, kinds: dict[str, Kind] = KINDS, rates=load_rates):
        self.app, self.kinds, self.rates = app, kinds, rates

    def kind(self, name: str) -> Kind:
        if name not in self.kinds:
            raise ValueError(f"kind must be one of {', '.join(self.kinds)}")
        return self.kinds[name]

    def currency(self, value: str | None = None) -> str:
        value = self.app.profile.currency if value is None else value
        if not isinstance(value, str) or len(value) != 3 or not value.isalpha():
            raise ValueError("currency must be a three-letter code")
        return value.upper()

    def key(self, kind: str, query, sources: list, currency: str, bare: bool = False) -> str:
        """Empty optional fields are left out, so a query written before a field existed still finds its answer.
        `bare` leaves out the bars a source may apply as well: a recording made before them answers with all it
        has, and the view applies the bars."""
        asked = {
            name: value
            for name, value in asdict(query).items()
            if value != () and not (name in UNSET_IF_NONE and (value is None or bare)) and name != "max_night_eur"
        }
        return self.app.results.key(kind, asked, [s.name for s in sources], currency)

    def remembered(self, kind: str, query, sources: list, currency: str) -> Stored | None:
        key = self.key(kind, query, sources, currency, bare=self.app.replay)
        # A recording has no age: it is the only answer a replay can give.
        if self.app.replay:
            return self.app.results.recent(key, None, any_sources=True)
        stored = self.app.results.recent(key)
        # A search that brought nothing because its sources failed is not an answer worth repeating: ask again.
        if (
            stored
            and not stored.result["cards"]
            and any(r["status"] not in ("ok", "empty") for r in stored.result["sources"])
        ):
            return None
        return stored

    def estimate(self, kind: str, queries: list, sources=None, currency=None, refresh: bool = False) -> float:
        """Seconds of request spacing ahead. A query memory answers costs nothing; the queries of one call wait in
        the same queues, so their times add up."""
        searching, currency = self.kind(kind), self.currency(currency)
        chosen = searching.sources(sources)
        return sum(
            searching.estimate(query, chosen, self.app.limiter, self.app.net.exit)
            for query in queries
            if refresh or not self.remembered(kind, query, chosen, currency)
        )

    async def run(
        self,
        kind: str,
        queries: list,
        *,
        sources=None,
        currency=None,
        refresh: bool = False,
        confirm: bool = False,
        context: dict | None = None,
    ) -> list[Stored]:
        """Every query searched side by side, or taken from memory. Raises `NeedsConfirmation` before sending
        anything when the wait is over the profile's bar and `confirm` is not set. `sources` are names;
        `context` is kept with a new result in place of what the kind would find itself."""
        searching, currency = self.kind(kind), self.currency(currency)
        # One list of sources for every query of the call: a source can then keep one session for all of them.
        chosen = searching.sources(sources)
        seconds = self.estimate(kind, queries, sources, currency, refresh)
        if seconds > self.app.profile.confirm_over_seconds and not confirm:
            raise NeedsConfirmation(seconds)
        rates = await self.rates(self.app.net)
        return list(
            await asyncio.gather(
                *(
                    self._recall(searching, q, chosen, rates, currency, refresh, sources, len(queries), context)
                    for q in queries
                )
            )
        )

    async def _recall(self, kind: Kind, query, sources, rates, currency, refresh, selection, sharing, context):
        app = self.app
        stored = None if refresh and not app.replay else self.remembered(kind.name, query, sources, currency)
        if stored is not None:
            if app.replay:
                # The recording stands for the search that was asked for. Asked again when the first is too old to
                # reuse, or with `refresh`, it is a search made now.
                stored.fresh = True
                if refresh or app.results.clock() - stored.at > REUSE_SECONDS:
                    stored.at = app.results.clock()
                    app.results.touch(stored.id, stored.at)
            return stored
        if app.replay:
            raise ValueError(f"the replay has no {kind.name} search for {asdict(query)}")
        if context is None and kind.context is not None:
            context = await kind.context(app, query)
        # `sharing`: searches of one call wait in the same queues, so each gets that much more time.
        result = kind.to_json(await kind.search(query, sources, app.ctx, rates, currency, sharing=sharing), rates)
        result.update(context or {})
        if selection is not None:
            for report in result["sources"]:
                report["notes"].append("source selection: " + ", ".join(selection))
        return app.results.put(kind.name, self.key(kind.name, query, sources, currency), result)

    def stored(self, kind: str, search_id: str) -> Stored:
        stored = self.app.results.get(search_id) if isinstance(search_id, str) else None
        if stored is None:
            raise ValueError(f"no search {search_id!r} in memory (results are kept for 7 days); search again")
        if stored.kind != kind:
            raise ValueError(f"{search_id!r} is a {stored.kind} search, not a {kind} one")
        return stored

    def view(self, kind: str, stored: Stored, limit: int | None = 10, now: float | None = None, **filters) -> dict:
        """The cards of a search that pass the filters, cheapest first unless sorted otherwise, stamped with the
        search's identity and age. A filter left as None starts from the profile's bar. `limit` None shows all."""
        searching = self.kind(kind)
        asked = {name: value for name, value in filters.items() if value is not None}
        limit = max(1, len(stored.result["cards"])) if limit is None else limit
        shown = searching.view(self.app, stored.result, limit=limit, **{**searching.bars(self.app.profile), **asked})
        return stored.stamp(shown, self.app.results.clock() if now is None else now)

    def refine(self, kind: str, search_id: str, limit: int | None = 10, **filters) -> dict:
        """Another view of a search already made. No request to any site."""
        return self.view(kind, self.stored(kind, search_id), limit, **filters)
