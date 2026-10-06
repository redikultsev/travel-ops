"""Booking HTML adapter from docs/sources/booking.md.

Recipe attribution: Copyright (c) 2026 Suzzzzik, MIT fare-scraper.
https://github.com/Suzzzzik/fare-scraper/blob/main/stays.py
The applicable MIT license is retained in NOTICE.
"""

from urllib.parse import urlencode
from ...net.client import Blocked
from ..base import NotConfigured

URL = "https://www.booking.com/searchresults.html"
BANDS = (None, (0, 100), (100, 250), (250, 2000))


def challenge(response):
    headers = {str(k).lower(): str(v).lower() for k, v in response.headers.items()}
    low = response.body.lower()
    if response.status == 202 or headers.get("x-amzn-waf-action") == "challenge":
        return "Booking WAF refused the address"
    if response.status == 405 and b"captcha" in low:
        return "Booking returned a CAPTCHA"
    if len(low) < 3000 and (b"challenge" in low or b"captcha" in low):
        return "Booking returned an anti-bot challenge"
    return None


class Source:
    name = "booking"

    def max_requests(self, query):
        return 1 + len(BANDS)

    async def fetch(self, query, ctx):
        if query.children:
            raise NotConfigured("Booking child searches require ages absent from the query model")
        params = {
            "ss": query.place,
            "checkin": query.checkin.isoformat(),
            "checkout": query.checkout.isoformat(),
            "group_adults": query.adults,
            "no_rooms": query.rooms,
            "group_children": query.children,
            "selected_currency": "EUR",
            "order": "price",
        }
        # The token is short-lived; a stale one earns a challenge, which would quarantine the address.
        session = await ctx.browser.get(
            self.name, URL + "?" + urlencode(params), engine="chromium", ready_cookie="aws-waf-token", max_age=240
        )
        raws, faults = [], []
        try:
            for band in BANDS:
                page_params = dict(params)
                if band is not None:
                    page_params["nflt"] = f"price=EUR-{band[0]}-{band[1]}-1"
                response = await ctx.net.request(
                    self.name,
                    "GET",
                    URL,
                    params=page_params,
                    cookies=session.cookies,
                    impersonate=session.impersonate,
                    # No user-agent header: with the headless browser's own one Booking served a page without results.
                    headers={"accept-language": "en-GB,en;q=0.9"},
                    blocked_if=challenge,
                )
                if response.status >= 500:
                    # Booking's own fault page. It stands in for its band, which parse reports as missing.
                    faults.append(response.status)
                elif response.status != 200:
                    from ..base import ParseError

                    raise ParseError(f"Booking search returned HTTP {response.status}")
                raws.append(response.body)
        except Blocked:
            ctx.browser.drop(self.name)
            raise
        if len(faults) == len(raws):
            from ..base import SourceFault

            raise SourceFault(f"Booking answered every page with HTTP {faults[0]}")
        return raws

    def parse(self, raws, query, seen_at):
        import re
        from urllib.parse import urljoin, urlsplit
        from ...core.common import Link
        from ...core.money import Money
        from ...core.stays import Stay, Rate, StayOffer, kind_of_room
        from ..base import Parsed, ParseError
        from ._html import Tree, money

        unique, notes, unusable = {}, [], 0
        try:
            for index, raw in enumerate(raws):
                tree = Tree(raw).root
                cards = [node for node in tree.walk() if node.attrs.get("data-testid") == "property-card"]
                band = BANDS[index] if index < len(BANDS) else None
                label = "all prices" if band is None else f"EUR {band[0]}-{band[1]} nightly"
                notes.append(f"{label}: {len(cards)} cards")
                if not cards and not (
                    tree.find(data_testid="search-results")
                    or tree.find(id="search_results_table")
                    or "no properties found" in tree.text().lower()
                ):
                    # Booking now and then answers one of the requests with another page (its home page, for one).
                    # That loses a price band, not the search: the other pages still hold real offers.
                    notes[-1] = f"{label}: not a results page, this band is missing"
                    unusable += 1
                    continue
                for card in cards:
                    title, price = card.find(data_testid="title"), card.find(data_testid="price-and-discounted-price")
                    if not title or not title.text():
                        raise ParseError("property name missing")
                    if not price:
                        raise ParseError("property total price missing")
                    total = money(price.text())
                    href = next(
                        (n.attrs["href"] for n in card.walk() if n.tag == "a" and "/hotel/" in n.attrs.get("href", "")),
                        None,
                    )
                    if not href:
                        raise ParseError("property hotel link missing")
                    url = urlsplit(urljoin("https://www.booking.com", href))
                    if url.scheme not in ("http", "https") or url.hostname not in ("www.booking.com", "booking.com"):
                        raise ParseError("property URL is not Booking HTTP(S)")
                    source_id = url.path
                    link = (
                        "https://www.booking.com"
                        + source_id
                        + "?"
                        + urlencode(
                            {
                                "checkin": query.checkin.isoformat(),
                                "checkout": query.checkout.isoformat(),
                                "group_adults": query.adults,
                                "group_children": query.children,
                                "no_rooms": query.rooms,
                            }
                        )
                    )
                    review = card.find(data_testid="review-score")
                    text = review.text() if review else ""
                    score = re.search(r"Scored\s*(\d+(?:\.\d+)?)", text, re.I)
                    count = re.search(r"([\d,]+)\s+reviews", text, re.I)
                    rating = float(score[1]) if score else None
                    if rating is not None and not 0 <= rating <= 10:
                        raise ParseError("rating is outside 0-10")
                    image = next(
                        (
                            n.attrs.get("src")
                            for n in card.walk()
                            if n.tag == "img" and n.attrs.get("src", "").startswith(("https://", "http://"))
                        ),
                        None,
                    )
                    room_node = card.find(data_testid="recommended-units")
                    heading = next((n for n in room_node.walk() if n.tag in ("h3", "h4")), None) if room_node else None
                    room = heading.text() if heading else None
                    words = card.text().lower()
                    meals = "breakfast included" if "breakfast included" in words else None
                    # Taxes: "+€ 5 taxes and charges" is on top of the price; "Includes taxes and charges" is not.
                    taxes = card.find(data_testid="taxes-and-charges")
                    taxes_text = taxes.text() if taxes else ""
                    if "+" in taxes_text:
                        charges = money(taxes_text)
                    elif "includes taxes" in taxes_text.lower():
                        charges = Money(0, total.currency)
                    else:
                        charges = None
                    distance = card.find(data_testid="distance")
                    reach = (
                        re.match(
                            r"\s*([\d.,]+)\s*(km|m)\s+from\s+(?:the\s+)?(?:city\s+)?(?:centre|center|downtown)",
                            distance.text(),
                            re.I,
                        )
                        if distance
                        else None
                    )
                    center_km = None
                    if reach:
                        center_km = float(reach[1].replace(",", "")) / (1000 if reach[2].lower() == "m" else 1)
                    address = card.find(data_testid="address-link") or card.find(data_testid="address")
                    district = re.sub(r"\s*Show on map.*", "", address.text(), flags=re.S).strip() if address else None
                    offer = StayOffer(
                        Stay(
                            self.name,
                            source_id,
                            title.text(),
                            kind_of_room(room),
                            None,
                            None,
                            rating,
                            int(count[1].replace(",", "")) if count else None,
                            (image,) if image else (),
                            district=district or None,
                            center_km=round(center_km, 2) if center_km is not None else None,
                        ),
                        Rate(
                            total,
                            self.name,
                            self.name,
                            Link(link, "property"),
                            seen_at,
                            meals=meals,
                            room=room,
                            charges=charges,
                            # Only what a card says: silence is not "no free cancellation".
                            free_cancellation=True if "free cancellation" in words else None,
                            pay_at_property=True if "no prepayment needed" in words else None,
                        ),
                    )
                    if source_id not in unique or total.amount < unique[source_id].rate.total.amount:
                        unique[source_id] = offer
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ParseError(f"Booking card fields: {exc}") from exc
        if raws and unusable == len(raws):
            raise ParseError("no page was a search results page")
        if not unique:
            return Parsed([], notes)
        notes += [
            f"deduplicated: {len(unique)} properties",
            "bounded price-range coverage; inventory is not complete",
            "coordinates and amenities are not in search cards; stay_details reads them from the property page",
            "free cancellation is stated per card; its cutoff date is not",
            "missing ratings, review counts and photos remain unknown",
        ]
        return Parsed(list(unique.values()), notes)

    async def fetch_details(self, url, place, ctx):
        # The same short-lived token as a search: the browser earns it on a results page.
        session = await ctx.browser.get(
            self.name,
            URL + "?" + urlencode({"ss": place}),
            engine="chromium",
            ready_cookie="aws-waf-token",
            max_age=240,
        )
        try:
            response = await ctx.net.request(
                self.name,
                "GET",
                url,
                cookies=session.cookies,
                impersonate=session.impersonate,
                headers={"accept-language": "en-GB,en;q=0.9"},
                blocked_if=challenge,
            )
        except Blocked:
            ctx.browser.drop(self.name)
            raise
        if response.status != 200:
            from ..base import SourceFault

            raise SourceFault(f"Booking property page returned HTTP {response.status}")
        return response.body

    def parse_details(self, raw):
        """Facts from a property page. The property's own prose is left out: free text from a site is not for an
        agent."""
        import re
        from ..base import ParseError
        from ._html import Tree

        html = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        root = Tree(html).root

        def strings(node):
            for child in node.children:
                if isinstance(child, str):
                    if child.strip():
                        yield " ".join(child.split())
                else:
                    yield from strings(child)

        popular = root.find(data_testid="property-most-popular-facilities-wrapper")
        if popular is None and "hp_hotel_name" not in html:
            raise ParseError("not a Booking property page")
        amenities = [n.text() for n in popular.walk() if n.tag == "li"] if popular else []
        # Every facility of the property sits in the page data as Instance:{"id":16,"title":"Kitchenette"}.
        amenities += re.findall(r'Instance:\{\\"id\\":\d+,\\"title\\":\\"(.*?)\\"\}', html)
        spot = next((n.attrs["data-atlas-latlng"] for n in root.walk() if "data-atlas-latlng" in n.attrs), "")
        match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)\s*", spot)
        header = root.find(data_testid="PropertyHeaderAddressDesktop-wrapper")
        rules, check_in, check_out = [], None, None
        house = root.find(data_testid="HouseRules-wrapper")
        lines = list(strings(house)) if house else []
        topics = ("Child policies", "Age restriction", "Pets", "Cash only", "Parties", "Smoking", "Quiet hours")
        for index, line in enumerate(lines[:-1]):
            if line == "Check-in":
                check_in = lines[index + 1]
            elif line == "Check-out":
                check_out = lines[index + 1]
            elif line in topics:
                rules.append(f"{line}: {lines[index + 1]}")
        # Each subscore carries a label for screen readers: "Cleanliness, 7.8, Average rating out of 10".
        marks = re.findall(r">([A-Za-z][A-Za-z ]+), (\d+(?:\.\d+)?), Average rating out of 10<", html)
        scores = {name: float(value) for name, value in marks}
        photos = re.findall(r"https://cf\.bstatic\.com/xdata/images/hotel/max1024x768/\d+\.jpg\?k=[0-9a-f]+", html)
        return {
            "kind": None,
            "kind_as_listed": None,
            "lat": float(match[1]) if match else None,
            "lon": float(match[2]) if match else None,
            "address": next(strings(header), None) if header else None,
            "amenities": list(dict.fromkeys(amenities)),
            "not_available": [],
            "photos": list(dict.fromkeys(photos))[:30],
            "check_in": check_in,
            "check_out": check_out,
            "rules": rules,
            "scores": scores,
        }
