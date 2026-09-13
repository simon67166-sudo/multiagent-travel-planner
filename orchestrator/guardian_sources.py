"""Evidence-preserving Amap adapters; no fabricated routes or business matches."""
from copy import deepcopy
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

if __package__:
    from . import nearby_sources
else:
    import nearby_sources

DIRECTION_DOC = 'https://lbs.amap.com/api/webservice/guide/api/direction'
WEATHER_DOC = 'https://lbs.amap.com/api/webservice/guide/api-advanced/weatherinfo'
POI_DOC = 'https://lbs.amap.com/api/webservice/guide/api-advanced/search'
OFFICIAL_LINKS = [
    {'name': 'Hong Kong Transport Department', 'url': 'https://www.td.gov.hk/en/transport_in_hong_kong/public_transport/index.html'},
    {'name': 'Macau DSAT', 'url': 'https://www.dsat.gov.mo/'},
]
CITY_CODES = {'香港':'810000', '澳门':'820000', '澳門':'820000', 'Hong Kong':'810000', 'Macau':'820000', 'Macao':'820000'}


def _error(source, code):
    # Never expose upstream exception text or authenticated request URLs.
    return {'source': source, 'code': code, 'message': 'Source unavailable; no inferred data supplied.'}


def _request(endpoint, params):
    data = nearby_sources._amap(endpoint, params)
    if not isinstance(data, dict) or data.get('status') != '1':
        raise ValueError('Unavailable response')
    return data


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    if value is None or value == '' or value == []:
        return None
    if isinstance(value, bool):
        raise ValueError('Invalid number')
    try:
        number = float(value)
    except (TypeError, OverflowError):
        raise ValueError('Invalid number') from None
    if not math.isfinite(number) or number < 0:
        raise ValueError('Invalid number')
    return number


def _location(value):
    if isinstance(value, dict):
        if value.get('crs', 'GCJ02') not in ('GCJ02', 'GCJ-02'):
            raise ValueError('GCJ02 required')
        value = (value['lng'], value['lat'])
    elif isinstance(value, str):
        value = value.split(',')
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError('Coordinates required')
    if any(isinstance(v, bool) for v in value):
        raise ValueError('Invalid coordinates')
    lng, lat = map(float, value)
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise ValueError('Invalid coordinates')
    return f'{lng:.6f},{lat:.6f}'


def _polyline(value):
    if not value:
        return []
    return [list(map(float, _location(point).split(','))) for point in value.split(';') if point]


def _base(identifier, name, category, source, url, stamp, kind='real', amap_id=None):
    return dict(id=identifier, amap_id=amap_id, name=name, category=category,
                price=None, currency=None, unknowns=['price', 'currency', 'opening_hours'],
                source=source, source_url=url, fetched_at=stamp, data_kind=kind)


def route(origin, destination, city, mode='walking'):
    """Query v3 routes using GCJ02 coordinates; preserve transit segments verbatim."""
    result = _base('amap:route', 'Amap route', 'route', 'Amap', DIRECTION_DOC, None)
    result.update(available=False, status='unavailable', mode=mode, crs='GCJ02',
                  coordinates=[], coordinate_parts=[], steps=[], segments=[], errors=[],
                  duration_s=None, duration_min=None, distance_m=None,
                  official_links=deepcopy(OFFICIAL_LINKS))
    if mode not in ('walking', 'driving', 'transit'):
        result['errors'].append(_error('route', 'unsupported_mode'))
        return result
    try:
        if not _text(city):
            raise ValueError('City required')
        params = {'origin': _location(origin), 'destination': _location(destination)}
        endpoint = 'direction/' + mode
        if mode == 'transit':
            endpoint = 'direction/transit/integrated'
            params.update(city=city, cityd=city, extensions='all')
        data = _request(endpoint, params)
        result['fetched_at'] = data.get('_fetched_at')
        paths = data.get('route', {}).get('transits' if mode == 'transit' else 'paths', [])
        if not isinstance(paths, list) or not paths or not isinstance(paths[0], dict) or not paths[0]:
            raise ValueError('No route')
        path = paths[0]
        steps, parts = [], []
        if mode == 'transit':
            segments = path.get('segments', [])
            if not isinstance(segments, list) or not segments:
                raise ValueError('No transit segments')
            for segment in segments:
                if not isinstance(segment, dict) or not any(segment.get(k) for k in ('walking', 'bus', 'railway')):
                    raise ValueError('Empty transit segment')
                walking = segment.get('walking') or {}
                for step in walking.get('steps', []):
                    if step.get('instruction'):
                        steps.append(step['instruction'])
                    if step.get('polyline'):
                        parts.append(_polyline(step['polyline']))
                # buslines are alternatives, not consecutive legs: choose the first only.
                lines = (segment.get('bus') or {}).get('buslines', [])
                if lines:
                    line = lines[0]
                    if line.get('name'):
                        steps.append(line['name'])
                    if line.get('polyline'):
                        parts.append(_polyline(line['polyline']))
        else:
            segments = []
            if not isinstance(path.get('steps'), list) or not path['steps']:
                raise ValueError('No route steps')
            for step in path['steps']:
                if step.get('instruction'):
                    steps.append(step['instruction'])
                if step.get('polyline'):
                    parts.append(_polyline(step['polyline']))
        duration = _number(path.get('duration'))
        distance = _number(path.get('distance'))
        price = _number(path.get('cost')) if mode == 'transit' else None
        # Never connect disconnected geometry (not even with a straight fallback line).
        connected = []
        for part in parts:
            if not part:
                raise ValueError('Empty polyline')
            if connected and connected[-1] != part[0]:
                connected = []
                break
            connected.extend(part if not connected else part[1:])
        result.update(available=True, status='available', steps=steps, segments=deepcopy(segments),
                      coordinates=connected, coordinate_parts=parts, duration_s=duration,
                      duration_min=duration / 60 if duration is not None else None,
                      distance_m=distance, price=price, currency='CNY' if price is not None else None)
        result['unknowns'] = [key for key in ('price','currency','duration_s','distance_m') if result.get(key) is None]
    except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, ImportError):
        result['errors'].append(_error('route', 'unavailable'))
    return result


def _adcode(city):
    if not _text(city):
        raise ValueError('City required')
    if city in CITY_CODES:
        return CITY_CODES[city]
    if len(city) == 6 and city.isascii() and city.isdigit():
        return city
    data = _request('config/district', {'keywords':city, 'subdistrict':0})
    districts = data.get('districts', [])
    if len(districts) != 1 or not _text(districts[0].get('adcode')):
        raise ValueError('Ambiguous city')
    return districts[0]['adcode']


def weather(city):
    """City live observations and daily day/night forecasts, never hourly synthesis."""
    result = _base('amap:weather', 'Amap weather', 'weather', 'Amap', WEATHER_DOC, None)
    result.update(available=False, status='unavailable', city=city, errors=[])
    try:
        code = _adcode(city)
    except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, ImportError):
        result['errors'].append(_error('weather', 'city_unavailable'))
        return result
    for label, extension, field, granularity in [('live','base','lives','observation'), ('forecast','all','forecasts','daily')]:
        part = dict(available=False, records=[], granularity=granularity, spatial_granularity='city',
                    source='Amap', source_url=WEATHER_DOC, fetched_at=None, data_kind='real')
        try:
            data = _request('weather/weatherInfo', {'city':code, 'extensions':extension})
            records = data.get(field, [])
            if not isinstance(records, list) or not records or not all(isinstance(r, dict) and r.get('reporttime') for r in records):
                raise ValueError('No weather records')
            if label == 'forecast' and not all(isinstance(r.get('casts'), list) and r['casts'] for r in records):
                raise ValueError('No daily forecasts')
            if label == 'forecast':
                for record in records:
                    for cast in record['casts']:
                        if not isinstance(cast, dict) or not _text(cast.get('date')):
                            raise ValueError('Invalid daily forecast')
                        date.fromisoformat(cast['date'])
            elif not all(any(r.get(k) not in (None, '', []) for k in ('weather', 'temperature', 'humidity', 'windpower')) for r in records):
                raise ValueError('Empty live weather')
            part.update(available=True, records=deepcopy(records), fetched_at=data.get('_fetched_at'))
            result['available'] = True
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, ImportError):
            result['errors'].append(_error('weather.' + label, 'unavailable'))
        result[label] = part
    result['status'] = 'available' if result['available'] else 'unavailable'
    # Each response retains its own fetch time; a single shared timestamp would be misleading.
    return result


def fetch_context(city, location=None, when=None):
    """Fetch POIs independently of weather. `when` checks daily forecast coverage only."""
    result = dict(pois=[], weather={}, evidence=[], errors=[])
    try:
        if not _text(city):
            raise ValueError('City required')
        params = {'city':city, 'citylimit':'true', 'offset':25}
        endpoint = 'place/text'
        query_location = location
        if isinstance(location, dict):
            if 'keywords' in location:
                if not _text(location['keywords']):
                    raise ValueError('keywords must be a nonempty string')
                params['keywords'] = location['keywords']
            if 'location' in location:
                query_location = location['location']
                if location.get('crs', 'GCJ02') not in ('GCJ02', 'GCJ-02'):
                    raise ValueError('GCJ02 required')
            elif 'lng' not in location and 'lat' not in location:
                if 'keywords' not in params:
                    raise ValueError('Location or keywords required')
                query_location = None
        if query_location is not None:
            if isinstance(query_location, str) and ',' not in query_location:
                if not _text(query_location):
                    raise ValueError('Location must be nonempty')
                params.setdefault('keywords', query_location)
            else:
                endpoint = 'place/around'
                params.update(location=_location(query_location), radius=1500, sortrule='distance')
        if endpoint == 'place/text' and 'keywords' not in params:
            # Text search requires keywords or types; include service/public-facility categories.
            params['types'] = '050000|060000|070000|080000|090000|100000|110000|140000|150000|200000'
        city_code = CITY_CODES.get(city, city)
        bbox_city = {'810000':'香港', '820000':'澳門'}.get(city_code)
        data = _request(endpoint, params)
        pois = data.get('pois', [])
        if not isinstance(pois, list):
            raise ValueError('Invalid POIs')
        seen = set()
        for raw in pois:
            try:
                if not isinstance(raw, dict) or not _text(raw.get('id')) or not _text(raw.get('name')):
                    raise ValueError('Invalid POI')
                _location(raw['location'])
                poi = nearby_sources.amap_poi(raw, data.get('_fetched_at'))
                if bbox_city and not nearby_sources.in_city(bbox_city, poi['lng'], poi['lat']):
                    continue
                poi.update(_base(raw['id'], raw['name'], raw.get('type') or None, poi['source'], poi['source_url'], data.get('_fetched_at'), amap_id=raw['id']))
                if poi['category'] is None:
                    poi['unknowns'].append('category')
                if poi['id'] not in seen:
                    result['pois'].append(poi)
                    result['evidence'].append(deepcopy(poi))
                    seen.add(poi['id'])
            except (ValueError, TypeError, KeyError, AttributeError):
                result['errors'].append(_error('pois', 'invalid_record'))
    except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, ImportError):
        result['errors'].append(_error('pois', 'unavailable'))
    result['weather'] = weather(city)
    result['errors'].extend(result['weather']['errors'])
    for label in ('live', 'forecast'):
        part = result['weather'].get(label, {})
        if part.get('available'):
            evidence = _base('amap:weather:' + label, 'Amap ' + label, 'weather', 'Amap', WEATHER_DOC, part['fetched_at'])
            evidence.update(records=deepcopy(part['records']), granularity=part['granularity'])
            result['evidence'].append(evidence)
    if when is not None:
        try:
            requested = when.date() if isinstance(when, datetime) else when if isinstance(when, date) else datetime.fromisoformat(when.replace('Z', '+00:00')).date()
            dates = [cast.get('date') for record in result['weather'].get('forecast', {}).get('records', []) for cast in record.get('casts', [])]
            result['weather'].update(requested_date=requested.isoformat(), requested_date_available=requested.isoformat() in dates)
        except (ValueError, TypeError, AttributeError):
            result['errors'].append(_error('weather', 'invalid_when'))
    return result


def _safe_url(value):
    if not _text(value):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in ('http','https') and bool(parsed.hostname) and not parsed.username and not parsed.password


def _facts(value):
    """Copy a bounded JSON object without coercing or executing arbitrary objects."""
    if type(value) is not dict:
        raise ValueError('facts must be a JSON object')
    budget = [1000]

    def copy_value(item, depth=0):
        budget[0] -= 1
        if depth > 6 or budget[0] < 0:
            raise ValueError('facts exceed depth or node limit')
        if item is None or type(item) is bool:
            return item
        if type(item) in (int, float):
            try:
                finite = math.isfinite(item)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError('facts numbers must be finite')
            return item
        if type(item) is str:
            if len(item) > 20000:
                raise ValueError('facts string exceeds 20000 characters')
            return item
        if type(item) is list:
            return [copy_value(child, depth + 1) for child in item]
        if type(item) is dict:
            if not all(type(key) is str and key.strip() and len(key) <= 200 for key in item):
                raise ValueError('facts keys must be nonempty strings of at most 200 characters')
            return {key: copy_value(child, depth + 1) for key, child in item.items()}
        raise ValueError('facts must contain only JSON values')

    return copy_value(value)


def _validity(record):
    """Explicit validity bounds, inclusive start and exclusive end."""
    bounds = {}
    for key in ('valid_from', 'valid_until'):
        value = record.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(key + ' must be an ISO timestamp')
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError(key + ' must include timezone')
        bounds[key] = parsed
    if len(bounds) == 2 and bounds['valid_from'] >= bounds['valid_until']:
        raise ValueError('valid_from must precede valid_until')
    return bounds


def content_is_current(record):
    """No explicit expiry means unknown lifetime, not a freshness guarantee."""
    try:
        bounds = _validity(record)
    except (ValueError, TypeError):
        return False
    now = datetime.now(timezone.utc)
    return bounds.get('valid_from', now) <= now and ('valid_until' not in bounds or now < bounds['valid_until'])


def validate_content(records):
    """Validate an entire JSON-style list atomically; never infer identity from names."""
    if not isinstance(records, list) or len(records) > 10000:
        raise ValueError('Expected at most 10000 content records')
    output, seen = [], set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('Content record must be an object')
        _validity(record)
        kind = record.get('data_kind')
        if kind not in ('real','curated','demo') or not _text(record.get('id')) or not _text(record.get('name')):
            raise ValueError('id, name and explicit data_kind are required')
        if record['id'] in seen:
            raise ValueError('Duplicate content id')
        seen.add(record['id'])
        for key in ('amap_id','poi_id','city','kind','category','source','currency','fetched_at'):
            if record.get(key) is not None and not _text(record[key]):
                raise ValueError('Invalid content field: ' + key)
        if kind != 'demo' and not all(_text(record.get(k)) for k in ('source','source_url','fetched_at')):
            raise ValueError('Real and curated content require provenance')
        if record.get('source_url') is not None and not _safe_url(record['source_url']):
            raise ValueError('Source must be an HTTP(S) URL without credentials')
        stamp = record.get('fetched_at')
        if stamp and datetime.fromisoformat(stamp.replace('Z','+00:00')).tzinfo is None:
            raise ValueError('fetched_at must include timezone')
        price = _number(record.get('price'))
        if price is not None and not _text(record.get('currency')):
            raise ValueError('Price requires currency')
        unknowns = record.get('unknowns', [])
        if not isinstance(unknowns, list) or not all(_text(v) for v in unknowns):
            raise ValueError('unknowns must be a list of strings')
        normalized = _base(record['id'],record['name'],record.get('category'),record.get('source'),record.get('source_url'),stamp,kind,record.get('amap_id'))
        normalized.update(price=price, currency=record.get('currency'))
        normalized['unknowns'] = sorted(set(unknowns + [k for k in ('category','price','currency','amap_id','source','source_url','fetched_at') if normalized.get(k) is None]))
        # Retain inert text and bounded structured facts; ignore other fields.
        for key in ('text','summary','opening_hours'):
            if key in record:
                if record[key] is not None and not isinstance(record[key], str):
                    raise ValueError('Invalid content text')
                normalized[key] = record[key]
        for key in ('valid_from', 'valid_until', 'poi_id', 'city', 'kind'):
            if record.get(key) is not None:
                normalized[key] = record[key]
        normalized['facts'] = _facts(record.get('facts', {}))
        output.append(normalized)
    return output


def load_content(path):
    """Read local UTF-8 JSON only, <=2 MiB; no YAML, pickle, remote fetch or global mutation."""
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('Content file exceeds 2 MiB')
        return validate_content(json.loads(raw.decode('utf-8-sig')))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError('Unable to load content JSON') from None


def match_content(poi, records):
    """Return separate provenance-bearing records; demos never attach to real POIs."""
    validated = validate_content(records)
    identifier = poi.get('amap_id')
    return [r for r in validated if identifier and r['amap_id'] == identifier and r['data_kind'] != 'demo' and content_is_current(r)]


def provider_status():
    return {name: {'status':'unconfigured', 'available':False} for name in ('xhs','flyai','hotel','flight')}
