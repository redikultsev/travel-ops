# Google Flights

## Access

No API: Google closed QPX in 2018. The public search page
`https://www.google.com/travel/flights?tfs=…&hl=en&gl=US&curr=EUR` carries its result rows inline, in an
`AF_initDataCallback` block keyed `ds:1`. A plain GET with a Chrome TLS fingerprint reads it: no key, no browser.
The recipe is the open-source `fli` library (MIT, see NOTICE); the adapter sends its own requests through the
tool's limiter. A pre-answered consent cookie (`SOCS`) keeps an address in the EU from the consent page.

**Terms.** Google's terms of service do not allow automated access. The source is in the default list by the
Owner's decision; leave it out with `sources` or remove it from the registry where that matters.

## Request

`tfs` is a base64url protobuf: field 3 per direction (2 date, 13 origin, 14 destination, 4 a pinned flight),
8 one entry per traveller (1 adult, 2 child, 3 infant on a lap), 9 cabin (1 economy to 4 first), 19 trip type
(1 round trip, 2 one way).

## Answer

`ds:1[2][0]` and `ds:1[3][0]` are rows. A row's `[0][2]` lists its flights: `[3]`/`[6]` airports, `[20]`/`[21]`
dates and `[8]`/`[10]` local times, `[22]` carrier, number and operating carrier, `[16]` cabin. `[1][0][-1]` is
the price and `[1][1]` a protobuf token whose field 3.3 names the currency.

## Prices and limits

- A metasearch: the price is the cheapest a seller shows on Google, for the whole party. Google names the seller
  only on its own page, so the seller is `google` and the link opens that page for the same flights.
- A round trip is the outbound page, then one page per outbound tried with that flight pinned, which lists the
  returns at the round-trip price. The three cheapest outbounds are tried: four requests per route.
- Baggage and refund terms are not stated. Children are sent as Google counts them; Google itself lists fewer
  rows for parties with children.
- Ten seconds between pages; a refusal rests the source for an hour.

## Verified, 2026-10-07

Belgrade to Istanbul, 14 November, from a home address: one way 8 flights from 79 EUR (Air Serbia), one request;
returning 17 November, 15 round trips from 149 EUR, four requests.
