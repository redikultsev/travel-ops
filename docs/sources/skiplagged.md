# Skiplagged facts

## Access

Skiplagged publishes an MCP server at `https://mcp.skiplagged.com/mcp`. No key, no account, no session: a
`tools/call` of `sk_flights_search` answers without a handshake (tools/list and calls checked 2026-10-08). The
server has hotel, car and flexible-date tools as well; only the flight search is called.

## Request

`origin`, `destination` (IATA codes), `departureDate` and `returnDate` (YYYY-MM-DD), `adults`, `children`,
`infantsLap`, `fareClass` (economy, premium, business, first), `sort: price`, `limit: 100`, `maxStops: one`.
`limit` plus `offset` may not pass 100 ("Limit plus offset must not exceed 100"): there are no further pages.
Where `pagination.totalAvailable` is over 100 the same search is asked again with `sort: duration`, a second
hundred that overlaps the first. `pagination.hasMoreResults` is not reliable: it said false with 151 found and
100 shown.

`includeHiddenCity: false` emptied a route that had 300 fares (BEG–LIS, 2026-10-08): it is not sent, and
hidden-city cards are dropped by their `attributes`.

## Answer

`flights[]` cards: `id` (`BEG-LIS-2026-11-20-2026-11-24-trip=W64123-FR820,JU563`: the flights of each leg, the
way back after the comma), `departure` and `arrival` (airport and time with offset) of the whole leg,
`layovers`, `price` (`amount`, `currency` USD; for the whole party: 88 for one adult, 177 for two), `deepLink`
(its results page on that itinerary), `attributes` (`standard`, `virtual-interline`, `hidden-city`, `nonstop`,
`one-stop`), and `returnFlight` with the same fields for the way back.

A card does not say where a connection changes planes. A nonstop leg is whole; a connection is a chain of
flights that another source's itinerary of the same flights leaving at the same time completes. Chains no other
source has are left out and counted.

## Limits

- Hidden-city fares (ending before the ticketed destination: no checked bag, a missed flight breaks the rest)
  are left out.
- Virtual interlining: airlines that do not sell together joined on several tickets, booked on Skiplagged.
- Baggage is not stated. Codeshare numbers appear as their own flights (EY7955 for an Air Serbia flight).

## Verified, 2026-10-08

BEG–IST one way 14 November: 140 counted, 113 read from two answers; in a search with every source 84 of the
first 100 joined other sources' itineraries, and Skiplagged was the cheapest seller on several (JU102 TK1046
105.37 EUR against Kupibilet 113.46). BEG–LIS round trip 20 to 24 November: 300 counted.
