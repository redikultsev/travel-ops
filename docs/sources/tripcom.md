# Trip.com

## Access

No published API, and the search requests are signed by the page itself (`token`, `w-payload-source`,
`x-ctx-fvpc`, `x-ctx-wclient-req`, made per request by its JavaScript). They are not forged here. Instead a
browser (Camoufox) opens the results page as a person would, and the flight list the page receives from
`/restapi/soa2/27015/FlightListSearchSSE` is read. No key and no captcha on 2026-10-07 from a home address.
One page is one request of the source: twenty seconds apart, an hour's rest after a refusal.

## Request

`https://www.trip.com/flights/showfarefirst?dcity=beg&acity=ist&ddate=2026-11-14&triptype=ow&class=y&quantity=1&locale=en-XX&curr=EUR`
— lower-case IATA codes, class `y`/`s`/`c`/`f`, `quantity` the adults.

## Answer

A stream of `data:` frames; the one with `itineraryList` holds up to 25 itineraries. Each has `journeyList`
(sections with `flightInfo.flightNo`, airports and local times without a zone) and `policies`: the price
(`price.adult.totalPrice`, taxes included, and `totalPrice`, the party's sum rounded) and tags
(`FREE_CARRY_ON_BAGGAGE`, `FREE_CHECKED_BAGGAGE`). Only these and the currency are kept; the rest of the answer
names the visitor's session and the server's logs.

## Prices and limits

- Round trips: the page (`triptype=rt&rdate=…`) lists the outbound flights at a round-trip price, and the returns
  appear only once one is chosen. Each fare's `shortPolicyId` names the flights it is priced with in its tail
  after the last `^`: per flight `{leg:2}{segment:2}{days:2}{digit}{code:2}{from}{to}{3 digits}{code:2}{length}
  {number}`, e.g. `0201040RS ISTBEG 040RD 5 JU423` — leg 2 (back), four days after the first departure, IST to
  BEG, JU423. Times are not in it, so the itinerary is partial: it is completed from another source's same
  flights on the same days, and left out and counted otherwise. BEG–IST 14 to 18 November (2026-10-08): 18 of
  25 fares completed; JU1106 JU1424 out and JU423 back 152.33 EUR, where Kupibilet's cheapest was 202.14.
- The price is one adult's ticket with taxes times the adults: two adults cost 311.84 EUR where one ticket is
  155.92 (round trip page, 2026-10-07). Children's fares are not verified: a party with children is not
  configured.
- A checked bag is stated only when the fare includes one; otherwise it is unknown, not none.
- Journeys with a train or bus section are left out and counted.
- A browser per route: about half a minute, and heavier than an HTTP source.

## Verified, 2026-10-07

Belgrade to Istanbul, 14 November, one adult: 25 itineraries from 82.64 EUR (Air Serbia), one page.

## Stays

The same rule: nothing signed is sent from here.

- **Where.** A place or a hotel becomes Trip.com's id through the page's own search box: the browser types the
  text into `input#destinationInput` on `https://www.trip.com/hotels/`, and the page asks
  `/restapi/soa2/34951/getHotelKeywords` (signed by the page). Each suggestion has `keyword.hotelInfo.hotelId` for
  a hotel and `controlInfo.regionInfo.basicCityModel.cityId` for its city. The city taken is the first suggestion
  within 60 km of the place's centre (`keywordContentInfo.coordinateItemList`, type `NORMAL`); without a centre,
  the first in the country named, by any of its names (Trip.com writes "Türkiye"). A city's id is kept for a
  month; the typing waits in its own line (`tripcom/lookup`).
- **Request.** `https://www.trip.com/hotels/list?city=2&checkin=2026-12-01&checkout=2026-12-30&adult=1&crn=1&children=0&curr=EUR&locale=en-XX`
  — `adult` the adults and `crn` the rooms; the page reports them back as filter `29` (`rooms|adults`). Adding
  `optionId=<hotelId>&optionType=Hotel&optionName=<name>` shows that one hotel. `searchWord=`, `keyword=` and
  `cityName=` were ignored on 2026-10-08 (the page showed Shanghai), so a place needs its id.
- **Answer.** The results page carries its list itself: Next.js writes it as string chunks
  (`self.__next_f.push([1, "..."])`), and `initListData.hotelList` holds about ten hotels. Each has `nameInfo`,
  `hotelCategory`, `commentInfo` (score out of `fullRating`, review count as text), `positionInfo.mapCoordinate`
  (BD09, GCJ-02 and WGS84 — only WGS84 is kept: in China the others are shifted by hundreds of metres) and one
  room, `roomInfo[0]`, with `priceInfoLayer.payInfo`: the rooms and nights before taxes and discounts, `payTax`,
  often `promotion` (the discounts, two thirds of Istanbul's hotels on 2026-10-08) and `total`. The total kept is
  `total`, to the cent, when it agrees within a euro with the card's `priceExplanation` ("Total price: €284 …
  incl. taxes & fees"), else the card's: a stay paid at the hotel showed €816 in `total` with `payTax` empty and
  €906 with taxes on the card. A room priced per bed says `1 bed ×` and is a dormitory.
- **Pages.** Scrolling to the end of the list makes the page ask `/restapi/soa2/34951/fetchHotelList` for the
  next twelve or so, signed by the page, in the same shape as `initListData`. Only a scroll to the very end
  asks (a wheel alone did not): nine scrolls gave 124 hotels for Shanghai. The browser scrolls up to twelve
  times, stopping when the list stops growing. No total is given.
- **Limits.** Ranked Trip.com's way, no filters passed; one room per hotel, the one the list shows. Children
  are not verified.
- **Verified, 2026-10-08.** Shanghai, 1–30 December, one adult: Orange Hotel (Shanghai Bund South Zhongshan
  Road) €1,777.14 + €106.61 taxes = €1,883.75. Okura Garden Hotel Shanghai by its id: €3,053 with taxes.
  Istanbul, 14–18 November, two adults: 124 hotels from 10 pages; the cheapest €136.38 in all.

## Not possible: Skyscanner

Probed the same way on 2026-10-07: the first answer held two itineraries at 699 EUR, and the next request of the
same search was refused by PerimeterX (`{"reason":"blocked"}`) with a "person or robot" captcha. Captchas are not
solved here, so Skyscanner is not a source.
