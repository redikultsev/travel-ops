# Where a place is

`travelops.geo` answers two questions about a place a human names: which airports serve it, and where its centre
is, for a stay's `center_km` and the `max_center_km` bar. Neither needs a key.

## The place: Open-Meteo's geocoder

`locate` asks `https://geocoding-api.open-meteo.com/v1/search` with the name and keeps the answer for a month.
Its data is GeoNames: `id` is the GeoNames id, `latitude`/`longitude` the GeoNames point, `feature_code` its
class (`PPLC` a capital). A country after a comma narrows the candidates. Airports are measured from this point:
a few kilometres do not change which airports serve a town.

## The centre: OpenStreetMap's place node, found through Wikidata

GeoNames puts some towns far from what a visitor calls the centre. On 2026-10-10 its Sarajevo point
(43.84864, 18.35644) lay 6.1 km west of Baščaršija, and a live stay search found 6 of 111 stays within 2 km.
So `centre` asks two more sources, one after the other, each answer kept for a month:

1. **Wikidata**, by the GeoNames id: `action=query&generator=search&gsrsearch=haswbstatement:P1566=<id>` with
   `prop=coordinates&coprimary=primary`. One request gives the town's item (`Q11194`) and its coordinate
   location (P625). The item is matched by id, so no name is guessed. Two items with one GeoNames id answer
   nothing.
2. **Nominatim**, by name and country code, with `extratags=1`. The answer that counts is the one whose
   `wikidata` tag is that item and which is OpenStreetMap's place node: `category=place`, or a boundary that
   Nominatim linked to its place node (`extratags.linked_place`), whose point is then the node's. A boundary
   without a linked node gives the middle of its area and is skipped. Belgrade's first answer is such a boundary
   (44.8153, 20.4457, 1.2 km from Republic Square), and its second is the place node.

The centre is the place node, else the item's coordinate, else the GeoNames point. `center.from` says which:
`openstreetmap`, `wikidata` or `geonames`. A point more than 25 km from the GeoNames point belongs to another
place and is not used. A source that fails, refuses or answers in another shape costs only its step. An answer
with an error in it (Wikidata's `maxlag`, a server error) is not served from the cache: it is asked again next
time.

Why this order. OpenStreetMap's convention for a place node is the central square, the town hall or the main
church ([Key:place](https://wiki.openstreetmap.org/wiki/Key:place)). Wikidata's P625 usually comes from the
Wikipedia infobox, and is often rounded: Istanbul's is 41.01, 28.96. On Novi Sad it is 1.5 km north of
Trg slobode, farther than GeoNames.

Measured live on 2026-10-10, as straight-line distance from a landmark of each historic centre:

| Place | Landmark | GeoNames | Wikidata P625 | OSM place node (used) |
|---|---|---|---|---|
| Sarajevo | Baščaršija | 6.13 km | 1.51 km | 1.53 km |
| Belgrade | Republic Square | 1.42 km | 0.30 km | 0.31 km |
| Istanbul | Sultanahmet | 2.46 km | 1.47 km | 0.13 km |
| Novi Sad | Trg slobode | 0.74 km | 1.48 km | 0.01 km |

Sarajevo's node and its Wikidata point are 80 m apart, on Trg oslobođenja in Centar, at the west end of
Ferhadija; Baščaršija is at the east end. On the stored Sarajevo search of that day (175 stays, all with
coordinates), stays within 2 km of the centre went from 10 to 82, and within 1 km from 5 to 26. Booking's own
"km from centre" figure is kept unless the straight line beats it by more than `OTHER_CENTER_KM`.

Tests: `tests/test_geo.py`, on the answers of that run in `tests/fixtures/geocoder`, `tests/fixtures/wikidata`
and `tests/fixtures/nominatim`.

## Usage policies

- Wikidata ([API etiquette](https://www.mediawiki.org/wiki/API:Etiquette),
  [User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy)):
  requests one at a time, a user agent with a contact URL. No `maxlag`: it is meant for bots that edit or
  crawl, and Wikidata counts the query service's lag in it (12.5 s on 2026-10-10), so with `maxlag=5` a read
  is refused almost always. Here it is one read per town a month, made while a human waits.
  Data CC0.
- Nominatim ([usage policy](https://operations.osmfoundation.org/policies/nominatim/)): one request a second at
  most, an identifying user agent, results cached, attribution. The limiter's `nominatim` rule spaces requests
  1.5 s apart; a town is asked once a month. Data ODbL: a centre from OpenStreetMap carries `center.credit`,
  "© OpenStreetMap contributors".
- Overpass would find the node by its `wikidata` tag directly, but on 2026-10-10 the main instance answered the
  second of four queries with 504. It is not used.
- No proxy, for any of them.

## Roads to the airports

`with_roads` asks FOSSGIS's OSRM (`routing.openstreetmap.de`, one request a second at most, a real user agent,
attribution) for the drive from the nearest airports to the place: one table request per trip, kept for a
month. A routing service that does not answer costs `km_road` and `minutes_road`, nothing else.
