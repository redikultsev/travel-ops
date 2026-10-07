# FlixBus

## Access

No published API. FlixBus's own shop searches through JSON at `https://global.api.flixbus.com/search`: no key,
no browser. City names are looked up first (`/autocomplete/cities`, cached for a month, on a short line of their
own), then one search (`/service/v4/search`).

## Request

`from_city_id`, `to_city_id`, `departure_date` as `DD.MM.YYYY`, `products` `{"adult": N}`, `currency: EUR`,
`search_by: cities`, `include_after_midnight_rides: 1`, and partner trips on (`disable_distribusion_trips: 0`).

## Answer

`trips[].results`: each trip has `status`, `price` (`total`, `total_with_platform_fee`), and `legs` with stations,
times with their zone, `means_of_transport` (bus or train) and `operator_id`. `stations` and `operators` name them.
Free text in `messages` is the carrier talking to travellers and is not kept.

## Prices and limits

- The price is for the whole party: two adults cost 81.60 EUR where one seat averages 40.80. FlixBus adds a
  service fee it requires (0.99 EUR here), so the price shown is `total_with_platform_fee`.
- FlixBus sells partner carriers too (`is_marketplace`): Belgrade to Vienna includes Fudeks.
- Children are priced by age group: a party with children is not configured.
- Sold-out trips are left out and counted. A place FlixBus reads as another name is said as a limit.

## Verified, 2026-10-07

Belgrade to Vienna, 14 November, two adults, from a home address: seven trips, direct and via Budapest, from
75.95 EUR for both. Three requests.
