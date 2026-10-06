# trivago facts

## Access

trivago publishes an MCP server for agents at `https://mcp.trivago.com/mcp`. No
key. It needs a session: `initialize`, `notifications/initialized`, then
`tools/call`. A call without a session answers `404 Invalid session ID`.

## Request

`trivago-accommodation-search` with `query` (a place name), `arrival`,
`departure`, `adults`, `rooms`, `currency: EUR`, `language: en`; children as a
count plus `children_ages` joined by dashes ("3-9").

## Answer

`accommodations[]` (25 on a page) with `accommodation_name`, `price_per_stay`,
`advertisers` (who sells at that price: Booking.com, Expedia, the hotel),
`hotel_rating` (stars, 0 when there are none), `review_rating` out of 10,
`review_count`, `top_amenities`, `latitude`, `longitude`, `distance`
("Kotor, 0.4 miles to City center"), `main_image` and `accommodation_url` (a
trivago page of that deal).

## Limits

- A metasearch: one advertiser's price per stay. The seller is recorded as
  `trivago:<advertiser>`; the link leads to trivago, which hands over to the
  advertiser.
- First page only; the total is not given.
- `top_amenities` is a short list: an amenity it names is there, one it does not
  name is unknown.
- The same property can also come from Booking.com directly. Sources are not
  deduplicated.
- The answer carries `system_message`, text addressed to a model, and inline
  images. Neither is kept: the adapter stores the list of stays only.
- trivago does not cover Russia.

## Verified, 2026-10-06

Kotor, 22 to 23 October 2026, one adult: 25 stays in EUR from a home address.
