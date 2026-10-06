# Stays

1. Read the profile and identify place, checkin, checkout and adults. Use the
   profile's stays.adults when omitted. Ask once for missing place/dates or any
   condition the query model cannot express. Keep the requested nights and
   party fixed; a nightly price is not a verified full-stay total.
2. Call `search_stays` once for the approved query and respect confirmation. The
   engine applies the profile's `min_rating` itself and says in `filtered` what it
   hid: stays below the bar, and separately stays with no rating at all.
3. Narrow with `refine_stays`, not by eye: `min_rating`, `min_reviews`, `max_total`,
   `exclude_kinds` (pass `shared_room` to drop dormitory beds), `sources`, `sort`.
   A `kind` of `other` means the source does not say what the place is; do not
   call it a private room. If nothing satisfies the requirements, say so and ask
   whether the human wants to see what a looser bar or the unrated ones hold.
   Do not claim that unknown amenities are absent or present.
4. Show a short set of cards: the total for the whole stay and party, the amount
   per night, the seller, the name, rating out of 10 with its review count, the
   room and meals when known, the cancellation terms when stated, and the property
   link carrying the requested dates. Keep unknowns visible in one line for the
   whole answer rather than under every card.
5. End with what the filters hid and the source report as AGENTS.md describes it.
   Booking and Airbnb are not deduplicated: the same property can appear under
   both, with different names and ratings.
