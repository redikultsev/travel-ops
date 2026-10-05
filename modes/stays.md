# Stays

1. Read the profile and identify place, checkin, checkout and adults. Use the
   profile's stays.adults when omitted. Ask once for missing place/dates or any
   condition the query model cannot express. Keep the requested nights and
   party fixed; a nightly price is not a verified full-stay total.
2. Call `search_stays` once for the approved query and respect confirmation.
3. Apply profile stays.min_rating and must_have to returned fields. Exclude
   known failures; a missing rating or amenity is unverified and cannot satisfy
   a hard minimum or must-have. Count known failures and unknown exclusions
   separately. If no offer satisfies the requirements, explain this and ask
   whether the human wants to inspect unverified alternatives. Do not claim
   that unknown amenities are absent or present.
4. Show a short set of cards with exact full-stay/whole-party total, per-night
   amount, source/seller, name, normalized rating out of 10 and review count,
   explicit cancellation cutoff, meals/room when known, one returned photo URL
   and the property link containing the requested dates. Keep unknowns visible.
   Show previews using their returned URLs; do not request galleries/details.
5. End with all source statuses, reasons, first-page/price-band coverage notes,
   and the number of cards displayed or excluded. Booking and Airbnb properties
   are not deduplicated across sources in v1.
