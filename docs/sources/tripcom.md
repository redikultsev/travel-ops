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

- One way only. A round trip lists the outbound flights at the price of the cheapest round trip, and the returns
  appear only once one is chosen on the page. Separate tickets search each way one way and include Trip.com.
- The price is one adult's ticket with taxes times the adults: two adults cost 311.84 EUR where one ticket is
  155.92 (round trip page, 2026-10-07). Children's fares are not verified: a party with children is not
  configured.
- A checked bag is stated only when the fare includes one; otherwise it is unknown, not none.
- Journeys with a train or bus section are left out and counted.
- A browser per route: about half a minute, and heavier than an HTTP source.

## Verified, 2026-10-07

Belgrade to Istanbul, 14 November, one adult: 25 itineraries from 82.64 EUR (Air Serbia), one page.

## Not possible: Skyscanner

Probed the same way on 2026-10-07: the first answer held two itineraries at 699 EUR, and the next request of the
same search was refused by PerimeterX (`{"reason":"blocked"}`) with a "person or robot" captcha. Captchas are not
solved here, so Skyscanner is not a source.
