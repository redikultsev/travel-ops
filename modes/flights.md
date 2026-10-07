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
3. When the human asks whether two one-way tickets are cheaper, or wants to
   leave from another airport than the one of arrival, search with
   `separate_tickets=true` (inside a trip it is the same parameter of
   `search_trip`). The engine searches each direction one way, pairs
   them and returns `separate_tickets`: the pairs, their sums in one currency,
   and what the best pair saves against the cheapest round trip. Always pass on
   its `risk`. It takes about three times as long as a plain search. For a party
   with children pass `children_ages`; name the sellers that could not price it.
4. The engine applies the profile's `max_stops` and `avoid_airlines` itself and says
   in `filtered` what it hid. Narrow further with `refine_flights`, not by eye:
   `checked_bag=true` when the profile or the human needs a checked bag, times of
   day, `airlines`, `destination`, `max_price`, `sort`. Fares in one group are the
   same product: same cabin, same answer to "is a checked bag included".
5. Show a short set of useful cards: exact best price and seller, up to two
   comparable competitors, every outbound/return flight with airport-local
   times, complete route, stops, airport changes and door-to-door duration.
   Include cabin, reported baggage, refundability and link kind. Show
   an airport change as an explicit transfer between those two airports. A round
   trip with `open_jaw: true` lands at one airport and leaves from another (or
   comes home to another): name both, since the stay starts at one and ends at the other.
6. End with what the filters hid and the source report as AGENTS.md describes it.
   A useful alternative may lack a validated link; flag it.
