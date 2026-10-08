"""Airbnb SSR adapter from docs/sources/airbnb.md.

Recipe attribution (MIT, licenses in NOTICE):
Copyright (c) 2026 Suzzzzik, https://github.com/Suzzzzik/fare-scraper/blob/main/stays.py
Copyright (c) 2024 John, https://github.com/johnbalvin/pyairbnb/blob/main/src/pyairbnb/standardize.py
"""

from urllib.parse import quote
from ..base import NotConfigured


def challenge(response):
    body = response.body.lower()
    if len(body) < 5000 and (b"captcha" in body or b"<title>challenge" in body or b"access denied" in body):
        return "Airbnb returned an anti-bot refusal"
    return None


# A results page holds 18 listings; its `paginationInfo.pageCursors` lead to the next ones (`cursor=`, verified
# 2026-10-08). PAGES is how far one search goes.
PAGES = 6
# Airbnb reads a place's name through a map search that can land on a street of that name in another town
# ("Istanbul, Turkey" became a street in Zonguldak, "Shanghai, China" Vancouver; 2026-10-08). With the place's
# centre known, the search names the map's box instead: BOX_KM each way from it (verified 2026-10-08).
BOX_KM = 15


def box(lat: float, lon: float, km: float = BOX_KM) -> dict:
    import math

    dlat, dlon = km / 111.0, km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return {
        "ne_lat": round(lat + dlat, 5),
        "ne_lng": round(lon + dlon, 5),
        "sw_lat": round(lat - dlat, 5),
        "sw_lng": round(lon - dlon, 5),
        "search_by_map": "true",
        "search_type": "user_map_move",
        "zoom": 11,
    }


def cursors_of(body: bytes) -> list[str]:
    import json
    import re

    match = re.search(rb'"pageCursors":(\[[^\]]*\])', body)
    try:
        return [c for c in json.loads(match[1]) if isinstance(c, str)] if match else []
    except ValueError:
        return []


class Source:
    name = "airbnb"

    def max_requests(self, query):
        return PAGES

    async def fetch(self, query, ctx):
        if query.rooms != 1:
            raise NotConfigured("Airbnb SSR searches do not represent multiple rooms")
        first = await self._page(query, ctx, None)
        raws = [first]
        for cursor in cursors_of(first)[1:PAGES]:
            try:
                raws.append(await self._page(query, ctx, cursor))
            except Exception as exc:  # a later page lost costs its listings, not the search
                from ...net.client import Blocked

                if isinstance(exc, Blocked):
                    raise
                break
        return raws

    async def _page(self, query, ctx, cursor):
        response = await ctx.net.request(
            self.name,
            "GET",
            "https://www.airbnb.com/s/" + quote(query.place, safe="") + "/homes",
            params={**({"cursor": cursor} if cursor else {}),
                **(box(*query.center) if query.center else {}),
                "checkin": query.checkin.isoformat(),
                "checkout": query.checkout.isoformat(),
                "adults": query.adults,
                # Airbnb counts under-twos apart; without ages every child is a child.
                "children": sum(1 for age in query.children_ages if age >= 2)
                if query.children_ages
                else query.children,
                "infants": sum(1 for age in query.children_ages if age < 2),
                "currency": "EUR",
            },
            headers={"accept-language": "en-US,en;q=0.9"},
            impersonate="chrome",
            blocked_if=challenge,
        )
        if response.status != 200:
            from ..base import ParseError

            raise ParseError(f"Airbnb search returned HTTP {response.status}")
        return response.body

    def parse(self, raws, query, seen_at):
        import base64
        import json
        import re
        from urllib.parse import urlencode
        from ...core.common import Link
        from ...core.stays import Stay, Rate, StayOffer, kind_of_listing, rating_out_of_10
        from ..base import Parsed, ParseError
        from ._html import Tree, money

        def contexts(node):
            if isinstance(node, dict):
                if isinstance(node.get("staysSearch"), dict):
                    yield node["staysSearch"]["results"]
                for value in node.values():
                    yield from contexts(value)
            elif isinstance(node, list):
                for value in node:
                    yield from contexts(value)

        def total_price(price):
            primary, secondary = price.get("primaryLine") or {}, price.get("secondaryLine") or {}
            for line in (secondary, primary):
                qualifier = " ".join(str(line.get(k) or "") for k in ("qualifier", "accessibilityLabel", "price"))
                explicit = "total" in qualifier.lower() or re.search(rf"\b{query.nights}\s+nights?\b", qualifier, re.I)
                if line is primary and price.get("displayPriceStyle") == "TOTAL_ONLY":
                    explicit = True
                if explicit:
                    value = line.get("discountedPrice") or line.get("price")
                    if value:
                        return money(str(value))

            def totals(node):
                if isinstance(node, dict):
                    if "total" in str(node.get("description", "")).lower() and node.get("priceString"):
                        yield money(node["priceString"])
                    for value in node.values():
                        yield from totals(value)
                elif isinstance(node, list):
                    for value in node:
                        yield from totals(value)

            candidates = list(totals(price.get("explanationData", {})))
            if candidates:
                return candidates[-1]
            raise ParseError("full-stay total missing; nightly rates cannot establish fees")

        offers = {}
        total_count, unpriced, monthly = None, 0, 0
        try:
            for raw in raws:
                tree = Tree(raw).root
                script = tree.find(id="data-deferred-state-0")
                if not script:
                    if any(n.tag == "form" and n.attrs.get("method", "").lower() == "post" for n in tree.walk()):
                        raise ParseError("Airbnb country-domain POST handoff; not submitted")
                    raise ParseError("Airbnb deferred search state missing")
                data = json.loads(script.text())
                found = list(contexts(data["niobeClientData"]))
                if not found:
                    raise ParseError("staysSearch.results context missing")
                for results in found:
                    listings = results["searchResults"]
                    if not isinstance(listings, list):
                        raise ParseError("searchResults is not a list")
                    pagination = results.get("paginationInfo") or {}
                    total_count = pagination.get("totalCount", total_count)
                    for item in listings:
                        if item.get("__typename") != "StaySearchResult":
                            raise ParseError("unknown search result typename")
                        listing = item["demandStayListing"]
                        source_id = base64.b64decode(listing["id"] + "===").decode().rsplit(":", 1)[-1]
                        if not source_id.isdigit():
                            raise ParseError("room identifier is not numeric")
                        name = listing["description"]["name"]["localizedStringWithTranslationPreference"]
                        coordinates = listing.get("location", {}).get("coordinate") or {}
                        rating_label = item.get("avgRatingLocalized") or ""
                        match = re.match(r"\s*(\d+(?:\.\d+)?)\s*(?:\(([\d,]+)\))?", rating_label)
                        rating = float(match[1]) if match else None
                        if rating is not None and not 0 <= rating <= 5:
                            raise ParseError("rating outside 0-5")
                        reviews = int(match[2].replace(",", "")) if match and match[2] else None
                        photos = tuple(
                            dict.fromkeys(
                                p["picture"]
                                for p in item.get("contextualPictures", [])
                                if isinstance(p.get("picture"), str)
                                and p["picture"].startswith(("http://", "https://"))
                            )
                        )
                        shown = item.get("structuredDisplayPrice") or {}
                        if shown.get("displayPriceStyle") == "MONTHLY":
                            # From 28 nights Airbnb shows a price a month, not the stay's total; it is not one.
                            monthly += 1
                            continue
                        try:
                            total = total_price(item["structuredDisplayPrice"])
                        except ParseError:
                            # A card that shows a nightly rate only: its fees are unknown, so it is left out, not
                            # the whole answer.
                            unpriced += 1
                            continue
                        url = f"https://www.airbnb.com/rooms/{source_id}?" + urlencode(
                            {
                                "check_in": query.checkin.isoformat(),
                                "check_out": query.checkout.isoformat(),
                                "adults": query.adults,
                                "children": query.children,
                            }
                        )
                        content = item.get("structuredContent") or {}
                        beds = [
                            line["body"]
                            for line in content.get("mapPrimaryLine") or content.get("primaryLine") or []
                            if isinstance(line, dict) and isinstance(line.get("body"), str)
                        ]
                        title = item.get("title") if isinstance(item.get("title"), str) else None
                        room = ", ".join(filter(None, [title, *beds])) or None
                        free = any(
                            message.get("type") == "FREE_CANCELLATION_HIGHLIGHT"
                            for message in item.get("paymentMessages") or []
                            if isinstance(message, dict)
                        )
                        offers[source_id] = StayOffer(
                            Stay(
                                self.name,
                                source_id,
                                name,
                                kind_of_listing(title),
                                coordinates.get("latitude"),
                                coordinates.get("longitude"),
                                rating_out_of_10(rating, 5),
                                reviews,
                                photos,
                            ),
                            Rate(
                                total,
                                self.name,
                                self.name,
                                Link(url, "property"),
                                seen_at,
                                room=room,
                                # Only what the card says: silence is not "no free cancellation".
                                free_cancellation=True if free else None,
                            ),
                        )
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ParseError(f"Airbnb search fields: {exc}") from exc
        month_note = (
            [
                f"{monthly} listings showed a monthly price, which Airbnb shows from 28 nights instead of the "
                "stay's total, and were left out: open the search on Airbnb for their totals"
            ]
            if monthly
            else []
        )
        if not offers:
            if unpriced and not monthly:
                raise ParseError("full-stay total missing on every card; nightly rates cannot establish fees")
            where = f"within {BOX_KM} km of the place's centre" if query.center else "for this place"
            return Parsed([], month_note or [f"Airbnb listed nothing {where} at these dates"])
        coverage = f"{len(offers)} listings from {len(raws)} pages of 18; " + (
            f"total {total_count}" if total_count is not None else "Airbnb gives no exact total"
        )
        return Parsed(
            list(offers.values()),
            [
                coverage,
                "amenities are not in search cards; stay_details reads them from the listing page",
                "taxes and fees inside or on top of the total are not stated",
                *([f"{unpriced} listings showed a nightly rate only and were left out"] if unpriced else []),
                *month_note,
            ],
        )

    async def fetch_details(self, url, place, ctx):
        response = await ctx.net.request(
            self.name,
            "GET",
            url,
            headers={"accept-language": "en-US,en;q=0.9"},
            impersonate="chrome",
            blocked_if=challenge,
        )
        if response.status != 200:
            from ..base import SourceFault

            raise SourceFault(f"Airbnb listing page returned HTTP {response.status}")
        return response.body

    def parse_details(self, raw):
        """Facts from a listing page. The host's own prose is left out: free text from a site is not for an agent."""
        import json
        from ...core.stays import kind_of_listing
        from ..base import ParseError
        from ._html import Tree

        script = Tree(raw).root.find(id="data-deferred-state-0")
        if not script:
            raise ParseError("Airbnb listing state missing")
        try:
            data = json.loads(script.text())["niobeClientData"][0][1]["data"]
            node = data["node"]
            page = node["pdpPresentation"]
            groups = page["amenities"]["seeAllAmenitiesGroups"]
            listed = [(a["title"], a.get("available") is not False) for g in groups for a in g["amenities"]]
            location = page.get("location") or {}

            def images(value):
                # Photos of the place are the `mediaItems` of the page sections; icons elsewhere are not photos.
                if isinstance(value, dict):
                    for item in value.get("mediaItems") or []:
                        if isinstance(item, dict) and isinstance(item.get("baseUrl"), str):
                            yield item["baseUrl"]
                    for item in value.values():
                        yield from images(item)
                elif isinstance(value, list):
                    for item in value:
                        yield from images(item)

            sharing = page.get("sharingConfig") or {}
            return {
                "kind": kind_of_listing(sharing.get("propertyType")),
                "kind_as_listed": sharing.get("propertyType"),
                "lat": float(location["latitude"]) if location.get("latitude") is not None else None,
                "lon": float(location["longitude"]) if location.get("longitude") is not None else None,
                "address": None,
                "amenities": list(dict.fromkeys(title for title, there in listed if there)),
                "not_available": list(dict.fromkeys(title for title, there in listed if not there)),
                "photos": list(dict.fromkeys(images(data.get("presentation") or {})))[:30],
                "check_in": None,
                "check_out": None,
                "rules": [],
                "scores": {},
            }
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ParseError(f"Airbnb listing fields: {exc}") from exc
