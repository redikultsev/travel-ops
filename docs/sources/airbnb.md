# Airbnb facts

## Sources and attribution

The SSR search recipe is derived from Suzzzzik's MIT fare-scraper `stays.py`
(https://github.com/Suzzzzik/fare-scraper/blob/main/stays.py).
Current listing, photo, rating, and total-price fields are referenced against
John's MIT pyairbnb standardize.py
(https://github.com/johnbalvin/pyairbnb/blob/main/src/pyairbnb/standardize.py).
Copyright (c) 2026 Suzzzzik; Copyright (c) 2024 John. Keep these in the adapter
header and NOTICE. The owner's two supplied research notes supplement them.
This adapter uses one SSR GET, not pyairbnb's API key and persisted-query path.

## GET and embedded state

GET `https://www.airbnb.com/s/<quoted-place>/homes` with `checkin`, `checkout`,
`adults`, `children`, `currency: EUR` and English Accept-Language, via Net with
Chrome impersonation. No browser session, credentials, or detail requests.
`rooms` is not a supported search parameter; report multi-room queries as not
configured rather than pretending that one listing provides multiple rooms.

The JSON script `id=data-deferred-state-0` contains `niobeClientData`. Inside it,
`staysSearch.results.searchResults` is the search-results list; wrappers can
change. Locate that exact searchResults context recursively, rather than
collecting arbitrary listings elsewhere in the page. Result objects have
`__typename: StaySearchResult`. The list and map can repeat the same listing;
deduplicate by listing ID. Disclose that only the first SSR page is shown, with
returned listing count and total count when the embedded pagination states one.
Never paginate or re-request a missing-state shell during implementation.

## Fields

`demandStayListing.id` is a base64 value ending in the numeric room ID after a
colon. Its `description.name.localizedStringWithTranslationPreference` is the
name; `location.coordinate.latitude` and `.longitude` are coordinates.
`avgRatingLocalized` can contain a 0–5 rating followed by a review count in
parentheses; double the rating for the normalized 0–10 scale. Unknown remains
null, not zero. `contextualPictures[*].picture` contains preview photo URLs;
deduplicate and keep HTTP(S) URLs without downloading images.

`structuredDisplayPrice` contains primaryLine (price, originalPrice,
discountedPrice, qualifier, accessibilityLabel), secondaryLine (a total price
when primary is nightly), and optional explanationData.priceDetails items.
Prefer an explicitly marked total for the full query; a primary price explicitly
qualified for the requested nights can be a total. Never treat an arbitrary
first number in a label as the total: labels can contain crossed-out prices,
night counts, and nightly prices. Preserve the numeric currency symbol/code
(EUR €, USD $, GBP £); unsupported/ambiguous currency is a parse error.
If only a clearly marked nightly rate is supplied, the presence of unknown fees
prevents deriving a complete total; return unparsed rather than inventing it.

Return `https://www.airbnb.com/rooms/<id>?check_in=<date>&check_out=<date>&adults=<n>&children=<n>`
as a property link. No booking endpoint. Cancellation dates, meals, and amenities
remain unknown unless explicitly established in the search payload.

## Refusals and budget

A small country-domain POST redirect form with no deferred state is a parse
error explaining the domain handoff; do not submit it or follow another country
through a workaround. 403/429/451 or explicit CAPTCHA/challenge pages are blocks.
Other missing-state HTML is unparsed, not an empty search.
At least 10 seconds plus up to 3 seconds positive jitter per request, at most 12
in 600 seconds, 30-minute quarantine on first block. Exactly one GET per search.

## Recorded response check

The single Belgrade query contains 18 `StaySearchResult` entries. The first room
ID is 947778502425423294, named Great location for a great price, at 44.8031,
20.4837. Its rating is 4.98 (173 reviews), first photo is a muscache URL, and
its discounted total is EUR 80 for two nights. The accessibility label mentions
the old EUR 89 price; use `discountedPrice`, not that last label amount.
`displayPriceStyle: TOTAL_ONLY` and qualifier `for 2 nights` confirm the total.
Pagination has cursors but no established total listing count; do not infer one.

The recorded test HTML retains only the actual search-results fields consumed
by the parser; unrelated page scripts, logging identifiers, wishlist state and
pagination cursors were removed before committing. The 18 listing records and
quoted prices are preserved, with the fixture scrubber applied again to JSON.

## Pages and long stays (2026-10-08)

- A results page holds 18 listings. Its `paginationInfo.pageCursors` (base64 of `section_offset` and
  `items_offset`) lead to the next pages with `cursor=<cursor>` on the same URL: the second cursor gave 17 new
  listings for Shanghai. A search reads up to six pages. The page says "1,000+ places", not an exact total.
- With `price_max` and `room_types[]` added, the page came back without listings: filters are not passed.
- From 28 nights a card shows a monthly price (`displayPriceStyle: MONTHLY`, "€5,075 monthly", "Average monthly
  price"), not the stay's total, and the listing page's own HTML carries no price. Such cards are left out and
  counted: a month in Shanghai found no Airbnb total to quote.
