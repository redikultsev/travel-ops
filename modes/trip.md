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
   - A card pairs one outbound with one return; the shortlist is the cheapest pairs, and always includes the
     cheapest one into each searched airport. `outbound_options` and `return_options` list the eight cheapest flights each way (`_total` counts
     the distinct flights that exist), each with the cheapest round trip it belongs to: use them for timings
     the cards do not show. Flying into one airport and out of another is not in a round-trip result; offer it
     as a next step (`separate_tickets`).
   - Check each pair against the stay: an early return on the last morning or a late arrival on the first
     night changes what the human gets from the nights paid for. Say so when it matters, and when the nearest
     airport offers only such a flight, look with `refine_flights` (`destination`, `return_after`) whether it
     has a better one before you answer.
   - A stay of kind `shared_room` is a bed in a dormitory. Say so, and when the cheapest stays are such beds
     also give the cheapest one that is not.
   - Read a stay as `modes/stays.md` step 3 says: the price with its stated taxes (`all_in`), and `center_km`.
     A stay far from the centre of a small town is not what "staying in Kotor" asks for: prefer stays within
     about two kilometres, and say the distance of any stay you name.
   - A status other than `ok` or `empty` means that source's prices are unknown.
   - Lead with the cheapest offer and its link. When only a dearer seller has an exact `ticket` link, mention
     that offer too rather than swapping it in.
4. Answer in this order, short, as lists (no tables: many chat clients do not render them):
   - one line with what you searched: route, airports with distances, dates, party, defaults you relied on;
   - flights: two or three choices that differ in a way that matters (cheapest, best timing, other airport),
     each with flight numbers and local times both ways, the price in its own currency with the converted
     amount, the seller, and the link with its kind;
   - stays: two or three that pass the rating bar, each with the price for the stay, kind, distance from the
     centre, rating and review count, and the link;
   - one total: the cheapest round trip plus the cheapest stay that passes the bar, is not a dormitory bed
     and is near the centre, in the profile currency, from the returned converted amounts only, with the
     rates date;
   - what is not included: the transfer between the airport and the place (give the distance) and anything
     the sources do not price;
   - the report: how many offers exist, what the filters hid, and the source report as AGENTS.md describes it.
5. Close with one line of what you can do next: another airport, each direction as a separate ticket, other
   dates, more stays, a stricter or looser filter. Do not run new searches unasked.
6. Other shapes of a trip are parameters of the same call, not other tools:
   - one way ("I fly to Tbilisi on the 5th, three nights, no return yet"): omit `return_date` and pass
     `checkout` for the last day of the stay; with neither, only flights are searched and the result says so;
   - a stay shorter or longer than the flights: `checkout`;
   - "around the 22nd", "any day that week": `flex_days` from 1 to 3 shifts the whole trip by that many days
     each way and keeps its length. Stays are searched for the asked dates only: say so, and search stays again
     for the dates the human picks;
   - "is it cheaper as two tickets?", "into Tivat, out of Podgorica": `separate_tickets=true`. The result adds
     `separate_tickets.pairs`: one way out plus one way back, each with its own seller and link, the sum, and
     `open_jaw` when the airports differ. Always pass on `risk`: two tickets are two contracts. Say what the
     best pair saves against the cheapest round trip, or that it saves nothing. It takes about three times as
     long, so use it when asked or when you offered it and the human agreed;
   - children: `children_ages` with the age of each child on the travel dates; `adults` counts the grown-ups
     only. Some sellers cannot price a party with children and say so in their status: name them.
7. When the human then asks about what you already found (later flights, a bag, cheaper stays, no dormitories,
   more options), answer from `refine_flights` or `refine_stays` with the `search_id` of that part. Search again
   only for other dates, another place or party, or new prices.

A failed source or an empty part never cancels the rest: give what came back and say plainly what did not.
