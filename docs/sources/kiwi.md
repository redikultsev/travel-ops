# Kiwi.com facts

## Access

Kiwi.com publishes an MCP server for agents at `https://mcp.kiwi.com`
(https://www.kiwi.com/en/pages/mcp/). No key, no account, no session: a
`tools/call` of `search-flight` answers without a handshake. One call per route.

## Request

`flyFrom`, `flyTo` (IATA codes), `departureDate` and `returnDate` as dd/mm/yyyy,
`adults`, `children`, `infants`, `cabinClass` (M, W, C, F), `currency: EUR`,
`sort: price`, `max_sector_stopovers: 1`. An answer holds at most 15 itineraries;
without the stop limit, chains of three low-cost flights fill it.

## Answer

`itineraries[]` with `price` (a number, for the whole party as Kiwi states it),
`bookingUrl` (a short link to that itinerary), `baggage` (counts across all
travellers), and `outbound` / `inbound` legs whose `segments` carry `from`, `to`,
`carrier`, `flightNumber` and local times.

## Limits

- Fifteen cheapest itineraries per route; the total is not given.
- Kiwi joins airlines that do not sell together. Such a connection is several
  tickets under Kiwi's own guarantee, not one through ticket. The answer has no
  flag for it: the adapter says so when a leg has more than one carrier.
- Kiwi does not sell flights to or from Russia.
- The tool description also tells a model how to lay out its reply. That text is
  never passed on: only the itineraries are read.

## Verified, 2026-10-06

BEG to TGD round trip 22 to 23 October 2026: 15 itineraries from 110 EUR. BEG to
TIV one way: 15 itineraries. Both from a home address, without a handshake.
