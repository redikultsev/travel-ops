# Kupibilet facts

## Request

POST once per requested cabin to `https://api-rs-lb.kupibilet.ru/frontend_search`.
No browser cookies are known to be required. Set origin and referer to
`https://www.kupibilet.ru` and send JSON. `trips` contains outbound and optional
reverse objects: `departure`, `arrival`, ISO `date`. `travelers` contains
`adult`, `child`, `infant`, and `childrenAges`. Cabin values are economy,
premium_economy, business, first. Other fields: `agent: context_b`, `language: RU`,
`currency: RUB`, `client_platform: web`, `filters.transport_kind: [airplane]`,
`sort_by: aggregated_cost`, `short_response: false`. Request one cabin only.
Child ages are absent from the v1 query model, so child searches cannot be
represented faithfully. Report that limitation rather than sending empty ages.

## Response mapping

Required root fields are `variants` (list) and `flights` (reference table).
Each variant has `segments[*].flights` (ordered reference IDs), `price.amount`,
optional explicit price currency, `baggage.weight`, and `hand_luggage.count`.
Preserve all flights and outbound/return boundaries. Each referenced flight has
`departure`, `arrival`, `departure_datetime`, `arrival_datetime`, `number`,
`marketing_carrier`, and `operating_carrier`. Date-times can carry offsets;
convert to airport-local aware times. Use the marketing carrier for the flight
number, falling back to operating carrier only when marketing is absent.

The fare cabin follows the requested cabin: a flight's own `cabin` describes the
aircraft and can still say economy in a business fare search. Baggage absent is
unknown; explicit zero weight means no checked allowance, positive weight proves
included baggage but does not establish the actual piece count. Do not invent
carry-on weight from a hand-luggage count. Refundability is not established.
RUB is the requested currency. Party-price behavior beyond one adult has not been
verified; do not invent a multiplication rule. Until verified, support one adult
and report other passenger compositions as not configured.

No exact-ticket URL or fully validated results URL was established. Return
`link: null` with an explicit note; never construct a guessed purchase URL.

## Limits

At least 5 seconds plus up to 1.5 seconds positive jitter between physical
requests; at most 30 in 600 seconds. Quarantine a blocked address for 30 minutes
on its first block. 403, 429, 451 and challenge HTML are blocks, not empty results.
No polling, cabin fan-out, retries, accounts, or checkout calls.

## Recorded response check

The recorded BEG–MOW query contains 535 variants and a map of referenced flights.
The first variant is JU134 BEG–SVO, RUB 37154, 18:30 +01:00 to 23:45 +03:00.
The recorded baggage object also exposes an explicit `count`; use it when
present, preserving unknown piece counts if only positive weight is available.
The first fare has checked count/weight zero and hand-luggage count one.
