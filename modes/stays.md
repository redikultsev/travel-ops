# Stays

1. Read the profile and identify place, checkin, checkout and adults. Use the
   profile's stays.adults when omitted. Ask once for missing place/dates or any
   condition the query model cannot express. Keep the requested nights and
   party fixed.
2. Call `search_stays` once for the approved query and respect confirmation. The
   engine applies the profile's `min_rating` itself and says in `filtered` what it
   hid: stays below the bar, and separately stays with no rating at all.
3. Read a card before you offer it:
   - Price: `total` is what the source shows first; `extra_charges` are the taxes
     and fees it lists on top (zero when included, null when it does not say);
     `all_in` is their sum. Quote `all_in` when it is there and say so; otherwise
     quote `total` and say that taxes and fees are not stated. Order and
     `max_total` already use `all_in` where known.
   - Place: `center_km` is the distance from the centre and `district` the
     source's name for the area. A cheap stay eight kilometres out is another
     trip: give the distance with every stay, and prefer `max_center_km` or
     `sort="center"` when the human wants to be in town.
   - Kind: `shared_room` is a bed in a dormitory; `room` a private room;
     `apartment` and `house` the whole place; `other` means the source does not
     say, so do not call it private.
   - `free_cancellation` true means the card states it; null means the card is
     silent, not that it is refused.
4. Narrow with `refine_stays`, not by eye: `min_rating`, `min_reviews`, `max_total`,
   `max_center_km`, `exclude_kinds`, `free_cancellation`, `sources`, `sort`. If
   nothing satisfies the requirements, say so and ask whether the human wants to
   see what a looser bar or the unrated ones hold.
5. Amenities, exact address, house rules and check-in times are on the property
   page, not on a search card. When the human needs one (wifi, kitchen, parking,
   pets, a late check-in) or the profile lists `must_have`, call `stay_details`
   for the two to five stays you would recommend, then `refine_stays` with
   `must_have`. A stay whose page was not read counts as unknown, never as
   lacking. Do not claim an amenity absent unless the page lists it under
   `not_available`.
6. When the human asks how a place looks, or wants one "bright", "with a view",
   "not shabby", call `stay_photos` and look. Say only what the photos show, name
   what they do not show, and give the links: the human cannot see what you were
   sent.
7. Show a short set of cards: the price as in step 3 and per night when more than
   one night, the seller, the name, the kind and room, rating out of 10 with its
   review count, distance from the centre, free cancellation and meals when
   stated, and the property link carrying the requested dates. Keep unknowns in
   one line for the whole answer rather than under every card.
8. End with what the filters hid and the source report as AGENTS.md describes it.
   Booking and Airbnb are not deduplicated: the same property can appear under
   both, with different names and ratings.
