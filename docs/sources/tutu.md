# Tutu facts

## Read-only MCP transport

Use only the official endpoint `https://mcp.tutu.ru/mcp`. The plan chooses this
transport for every route; the older city-ID HTTP interface is not used.
POST JSON-RPC 2.0 with `content-type: application/json` and
`accept: application/json, text/event-stream`. Initialize a session, retain the
`mcp-session-id` response header, send `notifications/initialized`, then use
`tools/list` for capabilities and `tools/call` for `search_avia` only.
The response can be ordinary JSON or SSE: JSON-RPC data appears in a `data:`
event. JSON-RPC errors and `result.isError` are failures, never empty searches.
The tool payload can be JSON text inside `result.content[*].text`.

Observed candidate arguments for `search_avia` are `from_city`, `to_city`,
`departure_date` (ISO), optional `return_date`, `adults`, `children`, `infants`,
`service_class`, `view: full`, `page` (starting at 1), and `page_size` (at most 30).
Candidate cabin codes are Y, S, C, F (economy, premium economy, business, first).
Cities may be names or IATA codes; prefer English names for Moscow and
Saint Petersburg and IATA for other places pending capability validation.
The candidate arguments and result shape have not yet been confirmed live.
Do not call any other tool, including passenger registration or checkout.
No account or credentials are used.

## Candidate result shape and its limits

A payload has `offers`. An offer has `price.amount` and `price.currency`,
`legs`, and `variants`. Legs are labeled `outbound` or `return`, with airport
labels such as `City (SVO)`, `departure_at`, `arrival_at`, `duration_min`, and
`segments[*].voyage_no` (for example JU-130). Fare variants have `service_class`
and `conditions`: `baggage.kg`, `cabin_baggage.kg`, `fare_family`, `refundable`.
Do not infer included baggage from absence. Numeric zero is explicit no baggage.
The answer states its price basis in `meta.pricing.basis`; only `party_total` is
accepted, so every price is for the whole party.

The known candidate shape only provides leg endpoints and times, and flight
numbers for connecting segments. It cannot establish each connecting airport
and time. Never fabricate those fields to fit the normalized model: reject
incomplete connections with a clear ParseError. A fully detailed segment shape
must first be observed and described before it can be supported.
An exact offer or results URL has not yet been established; preserve a returned
HTTP(S) offer link if capability/results validation confirms its field, otherwise
return no link. No link fields are currently assumed.

## Limits and refusals

Tutu publishes no request limit. Searches (`tools/call`) are spaced by 12 seconds
plus up to 3.6 seconds of jitter, at most 12 in 600 seconds. The two handshake
messages of a session (`initialize`, `notifications/initialized`) are not
searches: they wait about a second in a line of their own, as an MCP client
sends them. One handshake serves every route and date of a search. A refusal on
either line rests the whole source. A search reads up to six pages (30 offers each) while `meta.has_more` is true,
and reports the number received of `total_matched`.
A timeout can be an address ban; after the first timeout or refusal, record it
and do not run another live check during implementation. 403, 429, 451 and
challenge responses are blocks. The transport must never retry calls in a loop.

## Verified capabilities, 2026-10-05

One initialization/notification/tools-list sequence succeeded with three
physical requests spaced by the persistent limiter. The current tool schema
accepts preferred `origin` and `destination`; `from_city` / `to_city` remain
aliases. It supports `sort: price_asc` and `view: full`. Read up to six pages of 30 offers
while `meta.has_more` is true (2026-10-08), and report `meta.has_more`, `meta.total_matched`, and `meta.total_matched_exact`.
The official tool description explicitly states that every price is the whole
party total (`meta.pricing.basis: party_total`); the earlier uncertainty about
multiple adults is resolved for this transport.

The tool description also establishes `search_results_url` as a results-page
link (never a ticket link) and says full segment data is present. Its exact field
names will be documented after the first recorded search. `meta.from` / `meta.to`
report resolved places; IATA airport inputs narrow results, while city names
cover all airports. Relay any `meta.airport_note` and numeric
`meta.post_filter_dropped_*` counts. Relay `is_multi_pnr` / `multi_pnr_note`
without constructing a purchase flow. Checkout references, passenger tool
schemas, and the other advertised tools are neither called nor used.

## Recorded search, 2026-10-05

One BEG to Moscow search for 2026-11-14 returned 30 itineraries out of 45 matched,
with `meta.has_more: true` and exact total count. Each segment has `from`, `to`
(airport labels with IATA), `departure_at`, `arrival_at` (aware ISO timestamps),
and `voyage_no` such as JU-426. Each variant has its own `price.amount` / `.currency`,
`service_class` (ECONOMIC normalizes to economy), and `conditions` containing
`baggage.kg` / `.pieces`, `cabin_baggage.kg` / `.pieces`, `fare_family`,
`refundable`. Parse every priced variant, not only the offer's summary minimum.
Whole-party pricing is confirmed by `meta.pricing.basis: party_total`.

The first itinerary is JU426 BEG to IST, then PC386 SAW to VKO, priced at
25560.44 RUB in its cheapest variant. Airport change IST to SAW is explicit.
Its reported leg duration counts flight time only; compute elapsed travel time
from the first departure to final arrival instead. Keep airport-local aware
times. Returned `search_results_url` includes dates, class, and passenger count.
No checkout tool was called; checkout references and offer hashes are scrubbed
from the recorded fixture. The fixture is historical parser evidence, not a
price to present to a user during a future search.
