# Aviasales facts

## Search transport

The public search starts with POST `https://tickets-api.aviasales.ru/search/v2/start`.
The API sits behind AWS WAF. A Camoufox visit to the results page
`https://www.aviasales.ru/search/<ORIGIN><DDMM><DESTINATION><adults>` runs the WAF check and
sets the cookie `aws-waf-token`; the home page alone did not set it within seconds. Send all
cookies as cookies and as the semicolon-separated `x-origin-cookie` header, and send the token
again as the `x-aws-waf-token` header with `x-client-type: web`. Other observed headers are
`origin: https://www.aviasales.ru`, the same URL as `referer`, and `x-web-client: web_desktop`.
HTTP impersonation must match Firefox. Without the token the start request is refused with 403
from any address. The token is short-lived: a saved session is reused for at most four minutes.

The JSON request has `search_params.directions`, a list of objects with `origin`,
`destination`, ISO `date`, `is_origin_airport: false`, and
`is_destination_airport: false`. A return trip adds the reversed direction.
`search_params.passengers` contains `adults`, `children`, and `infants`.
`search_params.trip_class` accepts Y, W, C, F for economy, premium economy,
business, and first respectively. Use the requested class only. Observed outer
fields include `market_code: ru`, `marker: direct`, `citizenship: RU`,
`currency_code: rub`, `languages: {ru: 1}`, `brand: AS`, and
`search_source: search_form`. The browser also sends experiments and UI state;
these are not travel query facts and are not reproduced.

The start response contains `search_id` and `results_url` (a host name).
Results are POSTed from `https://<results_url>/search/v3.2/results` with
`search_id`, `limit: 1000`, `price_per_person: false`, `search_by_airport: false`, and
`last_update_timestamp`: zero on the first poll, then the stamp of the previous answer. This is
polling within one search, not repeated searches. A 304 means no update. At most 12 polls are
allowed; stop at `last_update_timestamp == 0`. Keep every answer of the search and drop repeated
offers afterwards: a later answer may carry only what changed. A search that never reached the
final stamp must be labeled partial.
Only accept an HTTPS results URL on an aviasales.ru host; do not follow arbitrary
hosts supplied in responses.

## Through the results page (2026-10-08)

An economy search without children, whose results page form is known, is made by the page itself: a browser
opens `https://www.aviasales.ru/search/<ORIGIN><DDMM><DESTINATION><adults>`, the page starts the search and polls
`/search/v3.2/results`, and its own answers are read until one has `last_update_timestamp: 0` (90 seconds at
most). The page shows the first tickets and loads more on request, so once done it asks once more, with its own
cookie and `x-aws-waf-token`, for all of them from stamp 0 with `limit: 1000`, on the results host its start
answer named (an aviasales.ru host only). Nothing leaves the browser. BEG–LIS 20 November: 903 offers, 78 cards
after the filters, one page visit, 36 seconds; the HTTP path had drawn a 403 twice that day with a fresh token.
Other searches (business, children) still go over HTTP with the page's token, as below.

## Response meaning

The results body is a JSON list of objects. Each object contains `tickets` and
`flight_legs`. Flight identifiers in `ticket.segments[*].flights` are indices
into that object's `flight_legs` list. Preserve every flight and the boundary
between outbound and inbound; an absent second segment means one way.

Each flight has `origin`, `destination`, `local_departure_date_time`,
`local_arrival_date_time`, and `operating_carrier_designator.carrier` / `.number`.
The local date-times are airport wall-clock times. Unix departure/arrival fields,
when available, are absolute times. The flight carries the operating carrier's designator.
Each proposal's `flight_terms` also has `marketing_carrier_designator`, and it differs between
sellers of the same flight (JU426 is also sold as TK9518), so the operating designator
identifies the physical flight.

Each ticket can have several `proposals`; keep each priced proposal, rather than
only its first price. `proposal.price.value` is the price. With
`price_per_person: false` the requested basis is the whole party. The requested
currency is RUB; if an explicit currency is returned, preserve it.
`proposal.flight_terms` uses stringified flight indices as keys. A term has
`trip_class`; `baggage.count` and `.weight`; `handbags.count` / `.weight` when
supplied; and `additional_tariff_info.fare_name` and
`.return_before_flight.available`. Missing baggage or refund fields mean unknown,
not zero or false. For a chain use conservative common baggage allowances and
retain unknown when any segment is silent. `proposal.agent_id` points into the
answer's `agents` map; its `gate_name` is the agency that sells the fare, so the
seller is `aviasales:<gate_name>`. The results page URL above opens the same search
on the site and is the link, of kind `results`; its form was observed for an economy
search without children only, so other searches return no link. An exact ticket URL
was not observed: do not invent one.

## Blocks and limits

403, 429, and 451 are refusals; an HTML page in place of the JSON answer is also a
block, even with success status. Words such as "captcha" inside a JSON answer are not. Missing start identifiers or missing
result fields are parse errors. No login, CAPTCHA solving, checkout, or purchase.

Use at least 12 seconds between physical requests, positive jitter up to 3.6
seconds, and at most 40 requests in 600 seconds. This deliberately applies the
new-search interval to continuations too. After the first block quarantine the
address for at least 30 minutes and do not retry it during implementation.
At most one recorded search is run at a time. Browser warmup shares the same
persistent bucket as HTTP; concurrent searches must not warm multiple browsers.

## Recorded response check

One BEG to MOW search for 2026-11-14 returned 200 tickets in the first poll, already final,
with 1458 priced proposals from nine agencies. The fixture keeps five of those tickets with
only the fields the parser reads. The first is JU130 BEG to SVO, 07:25 +01:00 to 12:40 +03:00,
RUB 27637 from the agency `aviasales`, no checked bag, hand luggage included.
