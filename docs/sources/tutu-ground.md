# Tutu trains and buses

## Access

The same read-only MCP server as Tutu flights, `https://mcp.tutu.ru/mcp`, with the
same session handshake. Only `search_rail` and `search_bus` are called. The server
also offers `create_checkout_link` and `register_checkout_passengers`; the second
creates an unpaid order that holds seats. Neither is ever called.

## Places

Tutu's index is in Russian. A Latin name goes through its suggest, which took
"Saint Petersburg" for Kolpino. The adapter asks the geocoder for the place's
Russian name first. Outside Russia the country follows it ("Сараево, Босния и
Герцеговина"): a bare Сараево resolved to a village in Bashkortostan. The answer's
`meta.from` / `meta.to` say what Tutu searched; when the name was ambiguous to
it (`also_named`, a `lead` match), the note says "understood as" and is reported
as a limit, so the agent checks the place.

## Request

`origin`, `destination`, `departure_date`, `page_size: 30`, `sort: price_asc`; further `page`s, up to three,
while `meta.has_more` is true.
Rail takes `passengers` (adults only); bus takes `adults` and `children`.

## Answer

`offers[]` with `price`, `legs[].segments[]` (`from`, `to` station names,
`departure_at` / `arrival_at` with their offsets, `carrier`, `voyage_no`),
`search_results_url` (Tutu's listing for the route and day), `review_summary`.
Rail offers add `price_party` and `fares.seat_categories` (one seat in each class).
Only `pricing`, `from`, `to`, `has_more` and `total_matched` of `meta` are kept; the
rest includes text the server addresses to a model.

## Prices

- Bus: `meta.pricing.basis` is `party_total`. Verified 2026-10-07: Belgrade to
  Vienna, Litas, 4242 RUB for one adult and 8484 RUB for two.
- Rail: `per_seat`. The party price is `price_party`, the cheapest class times the
  party, which holds only while that class still seats everyone: the card says
  `price_from`. Classes are named seat, open_berth, compartment, sleeper.
- A child's train fare is priced by age at booking and the search takes adults
  only: with children, trains are not searched and the note says so.

## Verified, 2026-10-07

Moscow to Saint Petersburg, 14 November, two adults: 34 trains and buses, from
2160 RUB (train 746У, seats). Belgrade to Vienna: one Litas bus. Belgrade to
Sarajevo: no bus or train at Tutu.
