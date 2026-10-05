# Trip

A trip is a place and two dates: "Montenegro, 22 to 23 October, staying in Kotor". Answer it in one pass.

1. Take what the human already said, then the profile; ask only for what neither gives. Needed: where from,
   the place to stay, both dates, the party. `origin` is IATA codes: the airport of the human's city (Belgrade is
   BEG) or a city code (MOW); if you are not sure which airports serve that city, ask `airports_near`. A date
   without a year is the next such date. Write the place in Latin script and pass its country separately:
   "Котор" in Montenegro is `place="Kotor", country="Montenegro"`. A country alone is not a place to stay: if
   the human named only a country or region, ask where in it, or offer two or three bases.
2. Call `search_trip` once with those facts. It finds the place, picks the nearest airports, searches flights
   there and back and stays for the same dates, side by side. Do not pick airports yourself; pass `airports`
   only when the human names one. If the result has `needs_confirmation`, say the estimate in minutes and wait.
3. Read the result before writing:
   - `place` is what the engine understood. If `other_places_with_this_name` could be what the human meant,
     say which one you searched.
   - `airports_in_reach` gives every airport with `km_straight` (a straight line, the road is longer) and
     whether it was searched. Name the searched ones with distances, and mention an unsearched one that is
     close, with its country if it differs.
   - A card pairs one outbound with one return. `outbound_options` and `return_options` list the eight
     cheapest flights each way (their `_total` says how many exist), each with the cheapest round trip it
     belongs to, its seller, link and `seen_at`: use them for timings the cards do not show. Flying into one
     airport and out of another is not in a round-trip result; offer it as a next step.
   - A stay of kind `shared_room` is a bed in a dormitory. Say so, and when the cheapest stays are such beds
     also give the cheapest one that is not.
   - Check each pair against the stay: an early return on the last morning or a late arrival on the first
     night changes what the human gets from the nights paid for. Say so when it matters.
   - `filtered` says what was hidden; `shown.of` counts the cards left after it, and for stays
     `shown.of_by_source` splits that count. A status other than `ok` or `empty` means that source's prices
     are unknown.
   - Lead with the cheapest offer and its link. When only a dearer seller has an exact `ticket` link, mention
     that offer too rather than swapping it in.
4. Answer in this order, short, as lists (no tables: many chat clients do not render them):
   - one line with what you searched: route, airports, dates, party;
   - flights: two or three choices that differ in a way that matters (cheapest, best timing, other airport),
     each with flight numbers and local times both ways, the price in its own currency with the converted
     amount, the seller, and the link with its kind;
   - stays: two or three that pass the rating bar, each with total for the stay, rating and review count,
     and the link;
   - a total for the cheapest sensible pair of flight and stay, in the profile currency, from the returned
     converted amounts only, with the rates date;
   - what is not included: the transfer between the airport and the place (give the distance) and anything
     the sources do not price;
   - the source lines and what was hidden: `filtered` and `shown` of both parts, and every source's status.
5. Close with one line of what you can do next: another airport, each direction as a separate ticket, other
   dates, more stays, a stricter or looser filter. Do not run those searches unasked.
6. When the human then asks about what you already found (later flights, a bag, cheaper stays, no dormitories,
   more options), answer from `refine_flights` or `refine_stays` with the `search_id` of that part. Search again
   only for other dates, another place or party, or new prices.

A failed source or an empty part never cancels the rest: give what came back and say plainly what did not.
