# OneTwoTrip facts

## Request

One GET to `https://www.onetwotrip.com/_avia-search-proxy/search/v3` returns one
requested cabin. No browser session is established by the observed transport.
The `route` string is DDMM + origin IATA + destination IATA, optionally followed
by the return DDMM. Cabin codes: E, W, B, F. Query parameters include `ad`, `cn`,
`in`, `cs`, `showDeeplink: true`, `source: google_adwords_organic`,
`priceIncludeBaggage: true`, `noClearNoBags: true`, `noMix: true`, `doNotMap: true`,
`srcmarker` (empty), and `cryptoTripsVersion: 61`.

Use a browser HTTP identity, `accept: */*`, `sec-fetch-site: same-origin`, and a
referer of `https://www.onetwotrip.com/ru/f/search/<route>?srcmarker2=newindex&sc=<code>&p=<ad>_<cn>_<in>`.
Do not send blank authorization or JSON content-type on this GET. Child and
infant counts were not validated in the available facts; unsupported counts must
be reported before requesting. Invalid cabins must not silently become economy.

## Response

The response can have its data at the root or under `data`. Required tables are
`prices`, `transportationVariants`, and `trips`; `references.services` contains
baggage definitions. Each price has `totalAmount` (whole-party total), optional
`deeplink`, `transportationVariantIds`, and optional directional `isRefundable`.
The queried market uses RUB; preserve explicit returned currency if present.
Each transportation variant has `directionIndex` (0 outbound, 1 return), ordered
`tripRefs` with `tripId` and `serviceClass`, and `serviceIds`. Preserve every trip,
not just the first. A trip has `from`, `to`, `startDateTime`, `endDateTime`,
`carrier`, and `carrierTripNumber`. Naive times are airport local times; convert
aware times to the airport zone. Cabin codes in the references are normalized
locally, including B for business. Do not compare a missing return as a round trip.

Services have `type` (`baggage` or `cabinBaggage`) and `code`. `0PC` explicitly
means no pieces; `nPC` means n pieces, not an established weight. `nK` means n kg,
not an established piece count (a positive value proves some included baggage).
An absent service is unknown, not zero. Aggregate allowances conservatively over
both directions. Refundability and baggage absence must remain unknown.

A nonempty deeplink is an exact ticket link; relative paths resolve against
`https://www.onetwotrip.com`. Accept HTTP(S) links only. If absent, use the
referer URL as a `results` link, preserving cabin and all passenger counts.

## Blocks and budget

The JSON error `ATTEMPTS_EXCEEDED` can arrive with HTTP 200; treat it as a block.
Also recognize 403/429/451 and CAPTCHA/challenge HTML. No retry after refusal.
Use at least 10 seconds with positive jitter up to 3 seconds per physical
request, at most 15 in 600 seconds, and a 30-minute first-block quarantine.

## Recorded response check

The single recorded BEG–MOW search on 2026-11-14 contains 102 prices. The first
price is RUB 29472.75, with JU426 BEG–IST and DP996 IST–VKO; airport-local naive
times produce a 1235-minute journey. One segment omits a cabin-baggage service,
so a whole-itinerary carry-on allowance is unknown. Service codes can combine
pieces and weight, such as `1PC23KG`; read both components when explicit.
