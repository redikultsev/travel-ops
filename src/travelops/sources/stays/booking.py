"""Booking HTML adapter from docs/sources/booking.md.

Recipe attribution: Copyright (c) 2026 Suzzzzik, MIT fare-scraper.
https://github.com/Suzzzzik/fare-scraper/blob/main/stays.py
The applicable MIT license is retained in NOTICE.
"""

import math
import re
from urllib.parse import urlencode
from ...net.client import Blocked
from ..base import NotConfigured, ParseError

URL = "https://www.booking.com/searchresults.html"
AUTOCOMPLETE = "https://accommodations.booking.com/autocomplete.json"
# A results page holds 25 properties and `offset` is ignored over HTTP, so a search walks up the price instead:
# each page asks for the cheapest above the dearest of the page before. PAGES is how far one search goes.
PAGES = 10
# With a ceiling asked the walk goes on to its end, the last page under the ceiling, and what it read is set
# against Booking's own count for the filters: properties whose price passes the ceiling only.
SWEEP_PAGES = 20
# Cards a page holds is Booking's to choose: 25 on 2026-10-08, 20 and 15 on 2026-10-10 for the same search. A
# page is taken as full when it shows fewer than Booking counts, whatever its size.
CARD = b'data-testid="property-card"'
CEILING = 10000  # EUR a night: the top of a price filter with no maximum asked
SCORES = (90, 80, 70, 60)  # Booking's review-score filter steps


def found_count(body: bytes) -> int | None:
    """How many properties Booking counts for the page's filters: "604 properties found"."""
    match = re.search(rb"([\d,]+) propert(?:y|ies) found", body)
    return int(match[1].replace(b",", b"")) if match else None


def filters(query, low: int, high: int) -> str:
    parts = [f"price=EUR-{low}-{high}-1"]
    if query.min_rating:
        score = next((s for s in SCORES if s <= query.min_rating * 10), None)
        if score:
            parts.insert(0, f"review_score={score}")
    return ";".join(parts)


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

    walked: bool = False  # the walk reached the last page of what passes the filters
    asked: bool = False

    def max_requests(self, query):
        return 1 + (SWEEP_PAGES if query.max_night_eur else PAGES)

    def _params(self, query):
        if query.children and len(query.children_ages) != query.children:
            raise NotConfigured("Booking prices a child by age: pass children_ages")
        params = {
            "ss": query.place,
            "checkin": query.checkin.isoformat(),
            "checkout": query.checkout.isoformat(),
            "group_adults": query.adults,
            "no_rooms": query.rooms,
            "group_children": query.children,
            "selected_currency": "EUR",
        }
        if query.children_ages:
            params["age"] = list(query.children_ages)  # one `age` per child
        return params

    async def _session(self, params, ctx):
        # The token is short-lived; a stale one earns a challenge, which would quarantine the address.
        return await ctx.browser.get(
            self.name,
            URL + "?" + urlencode(params, doseq=True),
            engine="chromium",
            ready_cookie="aws-waf-token",
            max_age=240,
        )

    async def lookup(self, query, name, ctx, seen_at):
        """One property by its name, at the dates of `query`: Booking's own search box names it, and a results
        page for that one property shows its card first. A results page for the name as text would show the
        whole city instead. The card is given the coordinates the search box states."""
        from dataclasses import replace

        params = self._params(query)
        session = await self._session(params, ctx)
        try:
            answer = await ctx.net.request(
                self.name,
                "POST",
                AUTOCOMPLETE,
                queue="lookup",
                json={"query": name, "language": "en-gb", "size": 5},
                headers={"origin": "https://www.booking.com", "referer": "https://www.booking.com/"},
                cookies=session.cookies,
                impersonate=session.impersonate,
                blocked_if=challenge,
            )
            hits = [h for h in (answer.json().get("results") or []) if h.get("dest_type") == "hotel"]
            if not hits:
                return []
            hit = hits[0]
            params.update(ss=hit.get("label") or name, dest_id=hit["dest_id"], dest_type="hotel")
            response = await ctx.net.request(
                self.name,
                "GET",
                URL,
                params=params,
                cookies=session.cookies,
                impersonate=session.impersonate,
                headers={"accept-language": "en-GB,en;q=0.9"},
                blocked_if=challenge,
            )
        except Blocked:
            ctx.browser.drop(self.name)
            raise
        if response.status != 200:
            from ..base import ParseError, SourceFault

            fault = SourceFault if response.status >= 500 else ParseError
            raise fault(f"Booking results for one property returned HTTP {response.status}")
        offers = self.parse([response.body], query, seen_at).offers
        named = str(hit.get("label1") or hit.get("label") or "").split(",")[0]
        if offers and hit.get("latitude") is not None:
            first = offers[0].stay
            offers = [
                replace(o, stay=replace(o.stay, lat=float(hit["latitude"]), lon=float(hit["longitude"])))
                if o.stay.source_id == first.source_id and o.stay.name.casefold() == named.casefold()
                else o
                for o in offers
            ]
        return offers

    async def fetch(self, query, ctx):
        from ..base import ParseError

        params = dict(self._params(query), order="price")
        session = await self._session(params, ctx)
        high = math.ceil(query.max_night_eur) if query.max_night_eur else CEILING
        raws, faults = [], []
        self.asked, self.walked = bool(query.max_night_eur), False

        async def page(low, top):
            response = await ctx.net.request(
                self.name,
                "GET",
                URL,
                params=dict(params, nflt=filters(query, low, top)),
                cookies=session.cookies,
                impersonate=session.impersonate,
                # No user-agent header: with the headless browser's own one Booking served a page without results.
                headers={"accept-language": "en-GB,en;q=0.9"},
                blocked_if=challenge,
            )
            if response.status >= 500:
                # Booking's own fault page: the walk stops there, and parse says how far it got.
                faults.append(response.status)
                return None
            if response.status != 200:
                raise ParseError(f"Booking search returned HTTP {response.status}")
            raws.append(response.body)
            return response.body

        try:
            await self._walk(query, ctx, page, high)
        except Blocked:
            ctx.browser.drop(self.name)
            raise
        if faults and not raws:
            from ..base import SourceFault

            raise SourceFault(f"Booking answered with HTTP {faults[0]}")
        if faults:
            raws.append(b"<!-- fault -->")
        return raws

    async def _walk(self, query, ctx, page, high):
        """Up the price, page by page: as far as PAGES go, or with a ceiling to its end. Cutting the price into
        bands read whole against their counts was tried for a proof of completeness and did not hold: pages of
        25, 20 or 15 cards, cards outside the filter (112 different properties where Booking counted 75), and 20
        pages in 14 minutes left it short (Belgrade under 400 EUR, 2026-10-10). The walk reads 67 of those 75 in
        four pages and says so."""
        from ..base import ParseError

        low = 0
        for _ in range(SWEEP_PAGES if self.asked else PAGES):
            body = await page(low, high)
            if body is None:
                return
            try:
                found = self.parse([body], query, ctx.now()).offers
            except ParseError:
                return  # not a results page: parse says so, and the walk cannot go on from it
            count = found_count(body)
            if count is None or count <= body.count(CARD):
                self.walked = count is not None
                return  # this was the last page of what passes the filters
            # A little below the last card's night: the filter may round, and a property seen twice is merged.
            # The last, not the dearest: Booking's order is not strictly by the price it shows (a card at 439 EUR
            # stood among ones up to 403 under a 45-a-night filter, 2026-10-10).
            top = found[-1].rate.total.amount / query.nights
            low = max(low + 1, math.floor(float(top) * 0.98))
            if low >= high:
                return

    def parse(self, raws, query, seen_at):
        import re
        from urllib.parse import urljoin, urlsplit
        from ...core.common import Link
        from ...core.money import Money
        from ...core.stays import Stay, Rate, StayOffer, kind_of_room
        from ..base import Coverage, Parsed, ParseError
        from ._html import Tree, money

        unique, notes, unusable, unavailable = {}, [], 0, 0
        try:
            for index, raw in enumerate(raws):
                tree = Tree(raw).root
                cards = [node for node in tree.walk() if node.attrs.get("data-testid") == "property-card"]
                if raw == b"<!-- fault -->":
                    notes.append("Booking failed on the next page (HTTP 5xx): the dearer properties are missing")
                    continue
                label = f"page {index + 1}"
                notes.append(f"{label}: {len(cards)} cards")
                if not cards and not (
                    tree.find(data_testid="search-results")
                    or tree.find(id="search_results_table")
                    or "no properties found" in tree.text().lower()
                ):
                    # Booking now and then answers one of the requests with another page (its home page, for one).
                    # That loses a price band, not the search: the other pages still hold real offers.
                    notes[-1] = f"{label}: not a results page, its properties are missing"
                    unusable += 1
                    continue
                for card in cards:
                    title, price = card.find(data_testid="title"), card.find(data_testid="price-and-discounted-price")
                    if not title or not title.text():
                        raise ParseError("property name missing")
                    if not price:
                        # A property shown for its name or its place but sold out at these dates has no price.
                        if "unavailable on our site" in card.text().lower():
                            unavailable += 1
                            continue
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
        real = [raw for raw in raws if raw != b"<!-- fault -->"]
        if real and unusable == len(real):
            raise ParseError("no page was a search results page")
        if unavailable:
            notes.append(f"{unavailable} properties shown as unavailable at these dates left out")
        counted = found_count(real[0]) if real else None
        ceiling = query.max_night_eur * query.nights if self.asked and query.max_night_eur else None
        # A page also shows properties outside its filter (a card at 439 EUR under 45 a night): only those under the
        # ceiling, to the euro a night the filter goes by, are what Booking counted.
        cap = math.ceil(query.max_night_eur) * query.nights if ceiling else None
        read = sum(1 for o in unique.values() if cap is None or o.rate.total.amount <= cap)
        coverage = Coverage(
            read,
            counted if ceiling else None,
            ceiling,
            bool(ceiling) and self.walked and counted is not None and read >= counted,
        )
        if not unique:
            return Parsed([], notes, coverage)
        if coverage.complete:
            reach = [f"everything Booking has under {ceiling:.0f} EUR for the stay was read: {read} of {counted}"]
        elif ceiling and counted is not None:
            reach = [f"under {ceiling:.0f} EUR for the stay {read} read of {counted} Booking counts: not all of it"]
        elif counted is not None and counted > len(unique):
            reach = [f"the {counted - len(unique)} not seen are dearer than the last page: lower max_total to reach them"]
        else:
            reach = []
        notes += [
            f"deduplicated: {len(unique)} properties"
            + (f" of {counted} Booking counts for these filters" if counted is not None else ""),
            *reach,
            "coordinates and amenities are not in search cards; stay_details reads them from the property page",
            "free cancellation is stated per card; its cutoff date is not",
            "missing ratings, review counts and photos remain unknown",
        ]
        return Parsed(list(unique.values()), notes, coverage)

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
