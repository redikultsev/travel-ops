# Flights

1. Read the profile and identify origin(s), destination(s), departure, optional
   return, date flexibility, party and cabin. Use home_airports for an omitted
   origin and profile defaults for omitted preferences. Ask once for required
   facts absent from both the request and profile. Resolve city names to their
   established IATA city codes (Moscow MOW, Saint Petersburg LED); clarify an
   ambiguous city. Add nearby airports only when the human requests or approves
   the expansion, and disclose that expansion. When the destination is a town rather
   than an airport, call `airports_near` and search the nearest ones.
2. Call `search_flights` once for the approved query. Respect its confirmation
   gate. Explain unsupported party compositions from the source reports; do not
   replace the party with one adult or multiply a one-person price.
3. With a return date, compare a round-trip search with the two corresponding
   one-way searches after approval for those additional searches. Compare the
   whole party and the same cabin/baggage conditions. Sum actual returned totals
   only in a common established currency. State separate-ticket/self-transfer
   risks and show when no fair comparison is possible. Never repeat a source
   that reported a block as part of this comparison.
4. The engine applies the profile's `max_stops` and `avoid_airlines` itself and says
   in `filtered` what it hid. Narrow further with `refine_flights`, not by eye:
   `checked_bag=true` when the profile or the human needs a checked bag, times of
   day, `airlines`, `destination`, `max_price`, `sort`. Fares in one group are the
   same product: same cabin, same answer to "is a checked bag included".
5. Show a short set of useful cards: exact best price and seller, up to two
   comparable competitors, every outbound/return flight with airport-local
   times, complete route, stops, airport changes and door-to-door duration.
   Include cabin, reported baggage, refundability and link kind. Show
   an airport change as an explicit transfer between those two airports.
6. End with what the filters hid and the source report as AGENTS.md describes it.
   A useful alternative may lack a validated link; flag it.
