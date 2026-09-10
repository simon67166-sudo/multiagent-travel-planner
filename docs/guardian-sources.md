# Guardian source adapter contract

Only guardian_sources.py implements these adapters. Import as orchestrator.guardian_sources, or guardian_sources with orchestrator on sys.path. No import-time network calls or content loading occur.

## Integration API

| Function | Contract |
| --- | --- |
| fetch_context(city, location=None, when=None) | Returns exactly pois:list, weather:dict, evidence:list, errors:list. POI and weather failures are independent. |
| route(origin, destination, city, mode='walking') | Actual Amap v3 walking, driving or transit response; unavailable status on unsupported/empty/failed responses. |
| weather(city) | Separate live observations and daily forecasts, preserving source records and timestamps. |
| validate_content(records) | Atomically validates and returns a new normalized list; raises ValueError for invalid records. |
| load_content(path) | Reads and validates local UTF-8 JSON; returns records without changing global state. |
| match_content(poi, records) | Returns separate real/curated records matching explicit amap_id only. |
| provider_status() | xhs/flyai/hotel/flight each reports status=unconfigured and available=false. |

Main integration owns storage, server and service wiring. Suggested content flow: load_content -> fetch_context -> match_content for each returned POI. Matching never overwrites POI fields; retain each record's provenance in the integration layer.

## Amap requests and limitations

All requests reuse nearby_sources._amap and therefore its existing get_json cache, throttle, timeout and credential lookup. No currentweather helper was found in the inspected Python sources. No credential files were read during implementation or testing. Exception text and authenticated request URLs are never returned in errors.

Routing calls direction/walking, direction/driving or direction/transit/integrated under https://restapi.amap.com/v3/. Transit sends city/cityd and extensions=all for same-city travel. Cross-city routing and departure-time selection are outside this route signature. Coordinates must be GCJ02: longitude,latitude string, pair, or lng/lat dict. Explicit non-GCJ02 CRS is rejected. Coordinate strings and pairs are interpreted as GCJ02; the caller is responsible for that CRS.

Results contain available/status, errors, mode, CRS, source metadata, duration_s, duration_min, distance_m, provider instructions and full transit segments. Missing numeric values remain null. Transit cost uses Amap's documented yuan unit (CNY), without inferring a local currency. Segments preserve provider stops, transfer details, railway fields and alternative bus lines. Render segments for full transit instructions; steps is only a short provider-text summary. The first route and first busline alternative are selected; alternatives remain in the raw segments.

Geometry comes only from provider polylines. coordinate_parts preserves separate parts; coordinates is populated only if all parts join. Never connect separated parts across gaps. Busline alternatives are not concatenated. Failed/empty/unsupported routes return available=false and empty geometry/instructions, plus official HK/Macau transport links. These links are references, not verified route plans. Live coverage and account permissions, including HK/Macau support, remain provider-dependent.

POI context accepts an optional search phrase or coordinates. No location means a broad city POI category search including shopping, daily services, recreation and public facilities; coordinates use place/around with 1500m radius and no types restriction. Results are capped at 25; this is not exhaustive retrieval. Search phrases do not identify content by business name. Invalid POIs are skipped with an error; duplicate Amap IDs are deduplicated.

Weather accepts a six-digit adcode, HK/Macau aliases, or an unambiguously resolved district name. It calls weather/weatherInfo separately with extensions=base and all. live.records and forecast.records preserve provider city/adcode, reporttime, observations and day/night casts. Each section carries its cached fetched_at, city spatial granularity, and observation/daily temporal granularity. One section can succeed while the other fails. Overall availability means at least one section succeeded; it does not promise requested-date coverage. Top-level fetched_at stays null because the sections may have different fetch times. Unavailable city resolution can omit both sections; consumers should use get() and available.

when accepts date/datetime or an ISO date/datetime string. It annotates requested_date and requested_date_available based on actual forecast dates. It does not modify source weather, interpolate hourly values, synthesize rain probabilities or claim that current observations describe a future trip. Invalid when adds an error.

## Normalization and content safety

POIs and evidence share id, amap_id, name, category, price, currency, unknowns, source, source_url, fetched_at, data_kind (real/curated/demo). Amap POI id and amap_id retain the provider ID. POI prices, currencies and opening hours are unknown; they are not inferred from names, categories or regional defaults. Weather evidence has a separate record per successful section, including its original records and granularity. Errors are dictionaries with source, code and a generic message.

Content records require unique nonempty id/name and explicit data_kind. Real/curated records require source, HTTP(S) source_url without embedded credentials, and timezone-aware ISO fetched_at. Required provenance is metadata validation, not independent confirmation of a submitter's claim. Optional amap_id must be an explicit string. Price must be finite/nonnegative and accompanied by currency. unknowns must be a list of strings. Optional text/summary/opening_hours are inert strings; UI consumers must escape them. Unrecognized fields are discarded. Maximum 10,000 records and 2 MiB per local JSON file. No YAML, pickle, remote URL fetching or content execution.

Example (illustrative only; replace every source and identity with actual evidence before importing):

```json
[{"id":"editor-note-1","amap_id":"B_EXPLICIT_PROVIDER_ID","name":"Editorial note","data_kind":"curated","source":"Named editor","source_url":"https://example.org/source","fetched_at":"2026-09-10T00:00:00+00:00","summary":"Source-grounded note"}]
```

match_content revalidates inputs. Only exact amap_id equality matches. Missing IDs, name equality and demo records never attach to a real business. Local content remains separate from live evidence; no synthetic business merging occurs. No OTA inventory, booking, flight/hotel pricing or social-platform access is advertised as available.

## Primary references consulted

- [Amap v3 routing and transit schema](https://lbs.amap.com/api/webservice/guide/api/direction)
- [Amap weather parameters, reporttime and daily casts](https://lbs.amap.com/api/webservice/guide/api-advanced/weatherinfo)
- [Hong Kong Transport Department](https://www.td.gov.hk/en/transport_in_hong_kong/public_transport/index.html)
- [Macau DSAT](https://www.dsat.gov.mo/)

## Verification

Run with E:\miniconda\envs\travelagent\python.exe:

```text
python -B -m unittest discover -s tests -p test_guardian_sources.py
python -B -m unittest discover -s tests -p test_nearby.py
```

Mocked tests cover walking/driving/transit, unsupported/empty/error responses, invalid coordinates, disconnected geometry, weather partial failure and malformed records, date coverage, provenance validation, file errors, ID-only matching, demo isolation, placeholder status and error redaction. The HTTP-boundary test replaces both requests and the credential provider, exercising the shared helper without actual keys or network calls. Mocked tests do not establish live Amap coverage.

## Structured facts for skills

Content records may include `facts`, an inert JSON dictionary retained through validate_content, load_content and match_content. Omitted facts defaults to `{}`. Keys are not restricted to particular skill names; for example:

```json
{"facts":{"diet":{"vegetarian":true,"allergens":["peanut"],"halal":null},"accessibility":{"wheelchair":"entrance only","steps":2},"photo_spots":[{"name":"terrace","time":"sunset"}]}}
```

These are source-backed content assertions, not automatically verified live business attributes. Skills should consume matching records' facts with their source/data_kind and preserve null as unknown. Facts are never merged into the Amap POI automatically.

Validation accepts only plain dictionaries, lists, strings, finite numbers, booleans and null. Limits per facts object: depth 6, 1,000 value nodes, 20,000 characters per string and 200 characters per nonempty string key. Arbitrary Python objects, callables, sets, NaN/infinity and excessive nesting are rejected. Returned facts are detached copies. UI consumers must escape all strings.

Missing credentials: the existing credential helper raises before get_json. The no-key test replaces that helper and verifies fetch_context and all route modes return unavailable/errors without any HTTP request or throttle sleep. No live API key is required for the test suite.

## Skill POI queries and geographic filtering

Use `fetch_context(city, location={"lng":113.54,"lat":22.19,"keywords":"公共厕所"})` for a real keyword query around coordinates. The dict also accepts `{"location":"113.54,22.19","keywords":"DIY"}` or `{"keywords":"购物"}` for city-limited text search. Keyword queries and coordinate searches do not carry the old attraction/restaurant types restriction. Any nonempty keyword is accepted; use provider-appropriate local-language search terms. Search results remain provider-ranked and capped at 25, so repeat skill-specific queries as needed. This does not promise a result for every skill.

All Hong Kong/Macau results are filtered with nearby_sources.in_city and its existing CITY bounding boxes, including traditional/simplified names, English aliases and 810000/820000. Outside-box results are omitted from both pois and evidence. These inherited boxes are coarse rectangles, not administrative polygons, and can exclude peripheral locations; consumers should not describe this as exhaustive territory coverage.

Route duration_s/duration_min stay null when absent, including transit. Main must require a known duration before scheduling a leg.

Additional primary reference: [Amap keyword and around POI search](https://lbs.amap.com/api/webservice/guide/api-advanced/search).
