# Wildberries Travel facts

## Transport

POST `https://travel.wildberries.ru/stream/api/avia-service/v3/stream/getFlights`.
The plan uses Camoufox cookies for the browser session. The observed HTTP facts
also include successful cookie-free requests; session necessity remains
unconfirmed for this installation. Do not add a browser fallback after a refusal.
Headers: origin `https://www.wildberries.ru`, referer with trailing slash,
`x-platform: web`, `client-session-id: site_<fresh random identifier>`,
`access-token: null`, `content-type: text/plain;charset=UTF-8`.
Send compact JSON as the request body, not a JSON-RPC envelope.

Body: `beginDate_at` as ISO date plus `T00:00:00.000Z`, `beginLocationCode`,
`endLocationCode`, `serviceClass`, `seats` with passenger ADT/CHD/INF and `number`,
`preferredAirlinesCodes: null`, `providers: [ot, ac, fs, pb]`,
`turnOnFolding: false`, `filter: {}`, `promoUUID` empty,
`includeCorporateTariffs: false`. A return adds `endDate_at` in the same format.
Cabins: ECONOMY, COMFORT, BUSINESS, FIRST. COMFORT is understood; a known
`404 FLIGHT_OPTIONS_NOT_FOUND` means no offers, not a reason to retry.

## Streaming response

The response is newline-delimited JSON. Each line has `result.data.flights`.
Chunks can be cumulative: keep the latest record for each flight `id`, rather
than counting each occurrence as another offer. Empty flights is a valid empty
search; unknown line shapes must raise a parse error.

A flight has `fullPrice` in minor RUB units (divide by 100), `legs`, `baggage`,
and `luggageMeta`. `fullPrice` is for the whole party:
one search on 2026-10-07 (MOW-IST, 2026-11-14) priced DP995 at 10210 RUB for one
adult and 20420 RUB for two. Child and infant fares are not verified and stay
not configured. A leg has
`startAirportCode`, `endAirportCode`, `dateBeginAt`, `dateEndAt`, and `segments`.
Each segment has `airlineCode` or `operationAirlineCode`, `flightNumber`,
`serviceClass`, `airportBeginCode`, `airportEndCode`, `dateBeginAt`, `dateEndAt`.
Preserve all segments. For this source a timestamp ending with Z represents
Moscow wall-clock time, not UTC: remove the Z, attach Europe/Moscow, then convert
to the appropriate airport time zone. Never apply that interpretation to other
sources.

`baggage.isIncluded` is explicit inclusion; `weightKg` is the allowance.
Missing inclusion remains unknown. `luggageMeta.cabin` indicates cabin luggage
when present. Extra per-flight tariff endpoints are intentionally not called:
they multiply traffic and are not necessary to show the summary price. State
that only summary fares are shown and detailed tariff terms are unknown.
There is no known results URL: `link` stays null and its absence is reported.

## Request budget

At least 8 seconds plus up to 2.4 seconds positive jitter per physical request,
at most 20 in 600 seconds. Browser navigation shares this same persistent budget.
First block: quarantine 30 minutes and do not retry during implementation.
403/429/451 and challenge/CAPTCHA HTML indicate blocks. Use one requested cabin,
no extra tariff requests, no booking, no credentials.

The passenger object keys are exactly `passenger` and `number`; send the ADT
count and zero CHD/INF entries.

## Recorded response check

The answer is a stream of JSON lines that ends only when the search does: it took about
half a minute and needs a longer deadline than an ordinary request. One MOW to LED search
for 2026-11-14 returned four lines and 51 flights. The fixture keeps six of them in one
line. The first is SU6215 SVO to LED, operated by FV, 11:30 to 13:00 Moscow time, RUB 6121.
