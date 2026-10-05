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
        raws = []
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
                if response.status != 200:
                    from ..base import ParseError

                    raise ParseError(f"Booking search returned HTTP {response.status}")
                raws.append(response.body)
        except Blocked:
            ctx.browser.drop(self.name)
            raise
        return raws

    def parse(self, raws, query, seen_at):
        import re
        from urllib.parse import urljoin, urlsplit
        from ...core.common import Link
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
                    meals = "breakfast included" if "breakfast included" in card.text().lower() else None
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
                        ),
                        Rate(total, self.name, self.name, Link(link, "property"), seen_at, meals=meals, room=room),
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
            "coordinates unavailable in search cards",
            "cancellation cutoff and unlisted amenities unknown",
            "missing ratings, review counts and photos remain unknown",
        ]
        return Parsed(list(unique.values()), notes)
