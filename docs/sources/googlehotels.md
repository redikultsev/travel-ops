# Google Hotels

## Access

No API. The Google Hotels page fetches its results with one call:
`POST https://www.google.com/_/TravelFrontendUi/data/batchexecute`, form body `f.req=[[["AtySUc","<json>",null,"1"]]]`.
A plain POST with a Chrome TLS fingerprint answers; no key, no cookie, no browser. The request and answer slots
are the open-source `stays` library's (MIT, see NOTICE), whose live tests run daily. The answer is a `wrb.fr`
frame for `AtySUc` holding JSON.

**Terms.** Google's terms of service do not allow automated access, as for Google Flights. Leave the source out
with `sources` where that matters.

## Request

`[text, [1, party, [null, dates], null, filters], meta]`:

- `text`: "<place> hotels" for a search, a hotel's name for a lookup.
- `party`: `null` for two adults, else `[[[3] per adult, [2,12] per child aged 2–12, [0,1] under 2, [13,17]], 1]`.
- `dates`: `[null, [[y,m,d], [y,m,d], nights], null, null, null, [null, children]]`.
- `filters`: `[[amenities, hotel classes, null, free cancellation, sort (3 = lowest price), null, "EUR", brands],
  null, [], [null, max per night or null, 1], guest rating]`; guest rating 7, 8 or 9 is 3.5, 4.0 or 4.5 of 5.
- `meta`: `[1, null, null, null, null, entity_key, 13, null, 0]`; an `entity_key` (from a search card's `[20]`)
  makes the answer that hotel's own, with every seller.

## Answer

Hotel entries are lists of 20 or more: `[1]` name, `[2][0]` `[lat, lon]`, `[3]` class, `[7][0]` `[rating of 5,
reviews]`, `[9]` map id, `[12]` photos, `[20]` entity key. `[6][2]` holds the price: `[1]` per night
(`["€25", null, 25.31, null, 25]`) and a last one-item list with the stay's total as shown (`["€101"]`, four
nights). In a hotel's own answer `[6][2][2]` lists sellers: `[0]` `[name, id, ad-click link, ...]` and a block
whose `[4]` is per night and `[5]` the total (`["223 €", null, 222.78, null, 223]`).

## Prices and limits

- About 18 hotels an answer and no pages. A search asks by lowest price and once per hotel class from 2 to 5
  stars: five requests, about 70 hotels.
- A search card's total is the cheapest any seller shows; Google names the seller only on its page. Whether a
  total includes taxes is not stated (the same hotel: Google's Booking.com 140 EUR where trivago's Booking.com
  was 126 EUR for four nights).
- A lookup (`compare_stays`) adds one rate per seller: `googlehotels:Booking.com`, `googlehotels:Agoda`. The
  sellers differ by hotel; Booking.com is among them for some hotels only. Their links are Google ad clicks, so
  the link given is Google's search for the hotel at the dates.
- Vacation rentals are a separate property type and are not asked for. One room only.
- Ten seconds between calls, as for Google Flights; a refusal rests the source for an hour.

## Verified, 2026-10-08

Istanbul, 14 to 18 November, two adults, from a home address: 70 hotels from five answers. Lookups: Nelly
Guesthouse — Booking.com 140 EUR and Bluepillow 140 EUR through Google, with Booking.com itself refusing us that
day; Taksim Trust Hotel — Bluepillow 132 EUR and TUI 227 EUR; Santa Sophia Hotel — Agoda 101, Vio.com 120,
TUI.com 135, Expedia.dk 223 EUR.
