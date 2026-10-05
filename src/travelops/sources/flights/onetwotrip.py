"""OneTwoTrip transport from docs/sources/onetwotrip.md."""

from __future__ import annotations

from itertools import product
from urllib.parse import urlencode

from ...core.flights import FlightQuery
from ..base import Context, NotConfigured

URL = "https://www.onetwotrip.com/_avia-search-proxy/search/v3"
SITE = "https://www.onetwotrip.com"
CLASSES = {"economy": "E", "premium_economy": "W", "business": "B", "first": "F"}


def results_url(query, origin, destination):
    route = query.depart.strftime("%d%m") + origin + destination
    if query.return_:
        route += query.return_.strftime("%d%m")
    params = {
        "srcmarker2": "newindex",
        "sc": CLASSES[query.cabin],
        "p": f"{query.adults}_{query.children}_{query.infants}",
    }
    return f"{SITE}/ru/f/search/{route}?{urlencode(params)}"


def challenge(response):
    low = response.body.lower()
    if b"attempts_exceeded" in low:
        return "OneTwoTrip rate-limited the address (ATTEMPTS_EXCEEDED)"
    if b"captcha" in low or (b"<html" in low and b"challenge" in low):
        return "OneTwoTrip returned an anti-bot challenge"
    return None


class Source:
    name = "onetwotrip"

    def max_requests(self, query: FlightQuery) -> int:
        return len(query.origins) * len(query.destinations)

    async def fetch(self, query: FlightQuery, ctx: Context) -> list[bytes]:
        if query.children or query.infants:
            raise NotConfigured("OneTwoTrip child/infant request mapping is not verified")
        if query.cabin not in CLASSES:
            raise ValueError(f"unsupported cabin: {query.cabin}")
        raws = []
        for origin, destination in product(query.origins, query.destinations):
            route = query.depart.strftime("%d%m") + origin + destination
            if query.return_:
                route += query.return_.strftime("%d%m")
            response = await ctx.net.request(
                self.name,
                "GET",
                URL,
                params={
                    "route": route,
                    "ad": str(query.adults),
                    "cn": str(query.children),
                    "in": str(query.infants),
                    "cs": CLASSES[query.cabin],
                    "showDeeplink": "true",
                    "source": "google_adwords_organic",
                    "priceIncludeBaggage": "true",
                    "noClearNoBags": "true",
                    "noMix": "true",
                    "doNotMap": "true",
                    "srcmarker": "",
                    "cryptoTripsVersion": "61",
                },
                headers={
                    "accept": "*/*",
                    "sec-fetch-site": "same-origin",
                    "referer": results_url(query, origin, destination),
                },
                blocked_if=challenge,
            )
            raws.append(response.body)
        return raws

    def parse(self, raws, query, seen_at):
        import json
        import re
        from urllib.parse import urljoin, urlparse
        from ...core.common import Link
        from ...core.flights import Baggage, Fare, FlightOffer, Itinerary, Segment, at_airport, cabin, flight_number
        from ...core.money import Money
        from ..base import Parsed, ParseError

        def allowance(service_ids, services):
            checked = weight = carry = None
            for sid in service_ids:
                service = services[sid]
                code = str(service.get("code", "")).upper()
                pieces = re.search(r"(\d+)PC", code)
                kg = re.search(r"(\d+)K(?:G)?", code)
                if service.get("type") == "baggage":
                    checked = int(pieces[1]) if pieces else None
                    weight = int(kg[1]) if kg else (0 if checked == 0 else None)
                elif service.get("type") == "cabinBaggage":
                    carry = (int(pieces[1]) > 0) if pieces else ((int(kg[1]) > 0) if kg else None)
            return checked, weight, carry

        offers = []
        halves = 0
        notes = [f"requested cabin: {query.cabin}"]
        try:
            for raw, (requested_origin, requested_destination) in zip(
                raws, product(query.origins, query.destinations), strict=True
            ):
                data = json.loads(raw)
                data = data.get("data", data)
                prices, variants, trips = (data[k] for k in ("prices", "transportationVariants", "trips"))
                if not all(isinstance(table, dict) for table in (prices, variants, trips)):
                    raise ParseError("prices, transportationVariants and trips must be maps")
                services = data.get("references", {}).get("services", {})
                for price in prices.values():
                    legs = {0: [], 1: []}
                    cabins, bags = [], []
                    for vid in price["transportationVariantIds"]:
                        variant = variants[vid]
                        direction = int(variant["directionIndex"])
                        if direction not in legs:
                            raise ParseError(f"unknown directionIndex: {direction}")
                        for ref in variant["tripRefs"]:
                            trip = trips[ref["tripId"]]
                            carrier = trip["carrier"].upper()
                            origin, destination = trip["from"], trip["to"]
                            legs[direction].append(
                                Segment(
                                    carrier,
                                    flight_number(carrier, trip["carrierTripNumber"]),
                                    origin,
                                    destination,
                                    at_airport(trip["startDateTime"], origin),
                                    at_airport(trip["endDateTime"], destination),
                                )
                            )
                            code = ref.get("serviceClass")
                            cabins.append("business" if code == "B" else cabin(code))
                            bags.append(allowance(ref.get("serviceIds", variant.get("serviceIds", [])), services))
                    if query.return_ and bool(legs[0]) != bool(legs[1]):
                        # A half of a pair of one-way tickets the site sells as a round trip. Alone it is not what was asked.
                        halves += 1
                        continue
                    if not legs[0]:
                        raise ParseError("missing outbound segments")
                    if legs[1] and not query.return_:
                        raise ParseError("unexpected return segments")
                    itinerary = Itinerary(tuple(legs[0]), tuple(legs[1]))

                    def minimum(column):
                        values = [bag[column] for bag in bags]
                        return min(values) if values and all(v is not None for v in values) else None

                    link_value = price.get("deeplink")
                    url = (
                        urljoin(SITE, link_value)
                        if link_value
                        else results_url(query, requested_origin, requested_destination)
                    )
                    if urlparse(url).scheme not in ("http", "https"):
                        raise ParseError("deeplink is not HTTP(S)")
                    refunds = price.get("isRefundable")
                    if isinstance(refunds, list):
                        refundable = (
                            False
                            if False in refunds
                            else (True if refunds and all(v is True for v in refunds) else None)
                        )
                    else:
                        refundable = refunds if isinstance(refunds, bool) else None
                    normalized_cabin = cabins[0] if cabins and all(c == cabins[0] for c in cabins) else None
                    offers.append(
                        FlightOffer(
                            itinerary,
                            Fare(
                                Money(price["totalAmount"], price.get("currency", data.get("currency", "RUB"))),
                                self.name,
                                self.name,
                                normalized_cabin,
                                Baggage(minimum(0), minimum(1), minimum(2)),
                                Link(url, "ticket" if link_value else "results"),
                                seen_at,
                                refundable,
                            ),
                        )
                    )
                    if price.get("isMultiticket") and "separate tickets may require self-transfer" not in notes:
                        notes.append("separate tickets may require self-transfer")
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            raise ParseError(f"OneTwoTrip response fields: {exc}") from exc
        if halves:
            notes.append(f"{halves} one-way halves of split round trips were left out; search each direction for them")
        return Parsed(offers, notes if offers or halves else [])
