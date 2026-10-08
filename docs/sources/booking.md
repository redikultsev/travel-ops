# Booking.com facts

## Sources and attribution

Session and HTML mapping ideas come from Suzzzzik's MIT fare-scraper `stays.py`
(https://github.com/Suzzzzik/fare-scraper/blob/main/stays.py), supplemented by
the owner's two supplied research notes. Copyright (c) 2026 Suzzzzik.
The adapter header and NOTICE retain this attribution. No login or official
partner API is required for this scraping path.

## Session and request

A Playwright Chromium visit obtains the `aws-waf-token` cookie, which is reused
with the same address and user agent via curl_cffi impersonating Chrome.
The browser and HTTP calls share the persistent source budget. The search page
is `https://www.booking.com/searchresults.html`; parameters are `ss` (place),
`checkin`, `checkout` (ISO), `group_adults`, `no_rooms`, `group_children`,
`selected_currency: EUR`, and `order: price`. Send English Accept-Language.
Children go as `group_children` plus one `age` parameter per child. Booking prices a
child by age, so a query with children but without every age is not configured.

Booking ignores `offset` over HTTP (verified again 2026-10-08: offsets 25 and 50 returned the same 25
properties). A search therefore walks up the price: with `order=price` and `nflt=price=EUR-<low>-<high>-1`
(nightly, EUR), each page asks for the cheapest at or above a little under the dearest night of the page before
(98%: the filter may round; a property seen twice is merged). It stops when a page has fewer than 25 cards, when
Booking counts 25 or fewer for the filters, or after ten pages. `<high>` is the search's `max_total` per night
when one is given, else 10000.

Each page says how many properties pass its filters ("604 properties found"); the first page's count is the
total for the search, so the result says how many of them were seen and that the rest are dearer. A
`min_rating` becomes `review_score=90|80|70|60`, rounded down (8.4 asks for 8+). Shanghai, 1–30 December, one
adult, 2026-10-08: rating 8+ hotels, 604; rating 8+ under EUR 60 a night, 293. A `distance=` filter was answered
with HTTP 502 twice and is not used: the view filters by distance.

## HTML fields

`data-testid=property-card` marks result cards. Within a card, `title` is the
property name and `price-and-discounted-price` is the quoted total for the query's
entire stay and party. `review-score` contains an English Scored N value on the
0–10 scale and a review-count label when supplied. `recommended-units` contains
the room heading. A `https://www.booking.com/hotel/...` href identifies the
property. Retain that path and add checkin, checkout, group_adults, no_rooms,
and group_children to the returned property URL. Do not make checkout URLs.

The first result-card image src is the preview photo. Return that URL; do not
request galleries or download photos. Coordinates are absent from the documented
card mapping; retain null when unavailable rather than inventing a location.
Cancellation and meals are reported only when explicit card text establishes
them (for example Breakfast included). Free cancellation without a cutoff does
not establish a date: keep `free_cancel_until` null. Do not invent amenities.
Absent rating, review count, or photo is unknown and must be visible as such.
Missing a price on a nonempty card is a parse error, never a fabricated zero.

## Blocks, empties, and budget

202 or `x-amzn-waf-action: challenge` means a WAF refusal. A short Challenge body
is also a block. Real HTML may include WAF scripts, so the word challenge in a
large otherwise-valid page is insufficient by itself. 405 CAPTCHA, 403, 429, and
451 are blocks. No CAPTCHA solving, account creation, proxy fallback, or retry
after the first refusal during implementation. A genuine 200 search page with
no property cards is empty; an unrelated HTML shell is unparsed.

Use at least 10 seconds plus up to 3 seconds positive jitter per physical
request, at most 12 in 600 seconds (one search can use eleven of them), and quarantine 24 hours after a WAF refusal.
Browser readiness is bounded; a browser that cannot obtain a session is blocked.

## Browser and request identity

The WAF token is minted in the installed Google Chrome (Playwright `channel="chrome"`,
headless, context locale en-GB) on the search URL itself; the bundled Chromium never received
it. The token arrives in under a second. The browser's own page loses the query and shows no
results, so it serves only to mint the cookie. The search pages are then fetched over HTTP with
those cookies, Chrome impersonation and `accept-language: en-GB`. Do not send the headless
browser's user-agent: with it Booking answered 200 with a page that had no results. The token
is short-lived: a saved session is reused for at most four minutes.

## Recorded response check

One Belgrade search for two adults, 2026-11-14 to 2026-11-16, returned 25 cards on the
unfiltered page and 61 distinct properties over the four price bands. The fixture keeps the
first six cards of the unfiltered page, without tracking query strings. The first is
Rooms for rent "SARA": EUR 45 for the stay, scored 9.0 from 252 reviews, Twin Room.

## One property by name

Typing a hotel's name as `ss` does not find it: the page shows the whole city by price
(Okura Garden Hotel Shanghai was not among 28 cards, 2026-10-08). The search box's own
suggestions do: `POST https://accommodations.booking.com/autocomplete.json` with
`{"query": <name, city>, "language": "en-gb", "size": 5}` and the session's cookies answers
`results[]` with `dest_id`, `dest_type` (`hotel` for a property), `label`, `label1`,
`latitude` and `longitude`. The search page with `dest_id` and `dest_type=hotel` then shows
that property's card first, followed by others nearby; its card is read as any other. The
suggestion waits in its own line (`booking/lookup`), the page in Booking's. `compare_stays`
uses it. Verified 2026-10-08: Okura Garden Hotel Shanghai, 1–30 December, one adult —
EUR 4,829 plus EUR 802 taxes, Executive Room with breakfast.
