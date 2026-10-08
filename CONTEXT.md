# Words of travel-ops

The terms the code and its tests are written in. A module named after one of them is where it lives.

**Kind** — what a search is about: `flights`, `stays` or `ground` (trains, buses, ferries and shared minibuses).
Everything that differs between kinds is in its Kind (`kinds.py`): how a query is read from tool arguments, which
sources answer it, how it is searched, stored and shown, the profile's bars, what a watch says of it. Code that
serves every kind asks the Kind; it does not branch on the name. A new kind is one entry in `KINDS`.

**Source** — one site or public server that answers a query (`sources/`): `fetch` goes to the network, `parse`
turns raw answers into offers without it. A source never fails a search: it gets a status and a reason.

**Query** — what was asked, after the profile filled the gaps: `FlightQuery`, `StayQuery`, `GroundQuery`.

**Search** — one query put to the sources of a kind, merged into cards, stored in memory for 7 days under a
`search_id` whose first letter is the kind. `searches.py` runs it: the same question within 30 minutes is answered
from memory, and a long wait raises `NeedsConfirmation` before anything is sent.

**View** — a stored search filtered, sorted and cut to a shortlist, stamped with the search's id and age. A view
never goes to the network. It starts from the profile's **bars** (max stops, longest leg, min rating, distance
from the centre) unless the caller sets them. `refine_*` tools are views.

**Card** — one thing to choose in a result: a flight itinerary with its fare groups, a stay with its rates, a ride
with its fare. **Offer** — one price for a card, from one seller, with its link and `seen_at`. A stay's card
holds every source's **listing** of one property (`listed_on`): merged by name and place, or added by
`compare_stays`, which looks the property up by name on the other sources.

**Trip** — flights to the airports near a place and back, and stays there, searched side by side (`trip.py`).

**Watch** — a saved search repeated by `travelops watch run` that alerts when its cheapest offer falls
(`watch.py`). Alerts wait for `watch_alerts`; each is given out once.

**Report** — what each source did in a search: status, reason, notes, requests. A view sums it up in `report`.
