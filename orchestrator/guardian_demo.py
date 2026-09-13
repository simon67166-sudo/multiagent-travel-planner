"""Reproducible FICTIONAL Hong Kong/Macau fixtures, never live merchant claims.

load_demo(city) returns a fresh full engine context. Dates, prices, queues,
workshops and venues are synthetic examples. No map coordinates, polylines or
external navigation routes are manufactured. 'walking_distance_m' values are
scenario inputs, not provider routes. Source timestamps are fixed fixture dates.
"""
from copy import deepcopy

STAMP = '2026-09-10T00:00:00+00:00'


def load_demo(city: str) -> dict:
    """Return independent context for HK/Hong Kong/香港 or Macau/Macao/澳门/澳門.

    Unknown cities raise ValueError. Members default empty; no person/preferences
    are invented. Includes canonical linked legacy trip and matching nearby stops.
    All records carry demo=True, data_kind=demo and explicit DEMO source labels.
    """
    if not isinstance(city, str):
        raise ValueError('city must be a string')
    key = city.strip().lower()
    if key in ('hk', 'hong kong', '香港'):
        city, currency, code = '香港', 'HKD', 'hk'
    elif key in ('macau', 'macao', '澳门', '澳門'):
        city, currency, code = '澳門', 'MOP', 'mo'
    else:
        raise ValueError('demo supports Hong Kong and Macau only')
    source = {'source': 'DEMO fictional scenario, not live business data', 'source_url': None,
              'fetched_at': STAMP, 'demo': True, 'data_kind': 'demo'}
    common = {**source, 'city': city, 'diet': ['vegan', 'vegetarian', 'peanut_free', 'halal'],
              'accessibility': True, 'internal_walking_m': 20, 'opening_hours': 'DEMO 09:00–18:00',
              'optional': True, 'cost_scope': 'per_person'}
    pois = [
        {**common, 'id': f'demo-{code}-garden', 'name': f'DEMO {city} 紙月花園茶屋（虛構）',
         'category': 'restaurant', 'price': {'amount': 40, 'currency': currency},
         'walking_distance_m': 300, 'indoor': False, 'queue_minutes': 45, 'crowd_level': 0.9,
         'soft': {'photo': 0.9, 'culture': 0.4, 'shopping': 0.3, 'rest': 0.6},
         'photo_spots': ['DEMO garden paper lantern display; photography permitted in fixture'],
         'toilet': True, 'products': ['DEMO paper lantern postcard'], 'cross_contact': 'unknown'},
        {**common, 'id': f'demo-{code}-museum', 'name': f'DEMO {city} 雲盒故事館（虛構）',
         'category': 'museum', 'price': {'amount': 20, 'currency': currency},
         'walking_distance_m': 200, 'indoor': True, 'queue_minutes': 5, 'crowd_level': 0.2,
         'soft': {'photo': 0.6, 'culture': 1.0, 'shopping': 0.2, 'rest': 0.9},
         'exhibits': ['DEMO harbour model', 'DEMO paper craft history'], 'toilet': True,
         'photo_spots': ['DEMO foyer display; no flash in fixture']},
        {**common, 'id': f'demo-{code}-craft', 'name': f'DEMO {city} 星砂手作小舖（虛構）',
         'category': 'shop', 'price': {'amount': 30, 'currency': currency},
         'walking_distance_m': 250, 'indoor': True, 'queue_minutes': 10, 'crowd_level': 0.4,
         'soft': {'photo': 0.5, 'culture': 0.7, 'shopping': 1.0, 'rest': 0.5},
         'products': ['DEMO folded-paper bookmark', 'DEMO woven card sleeve'], 'toilet': False},
    ]
    # Every detail below is scenario fiction, not public cultural documentation.
    pois[0].update(
        story='DEMO fiction: neighbours hang paper moons to welcome imaginary harbour travellers.',
        photo_angle='DEMO: eye-level three-quarter view of the paper lanterns, from the marked public area.',
        photo_time='DEMO scenario 10:00; not a measured lighting forecast',
        toilet_fee={'amount': 0, 'currency': currency}, floor='DEMO ground floor', tissue=True,
        products=[{'name': 'DEMO paper lantern postcard', 'price': {'amount': 8, 'currency': currency},
                   'recipient_tags': ['photography', '攝影', '拍照', 'postcards']}])
    pois[1].update(
        story='DEMO fiction: a paper boat carried a cloud into the harbour; the model introduces its imagined journey.',
        ordered_exhibits=['DEMO harbour model', 'DEMO paper craft history'],
        exhibits=[{'name': 'DEMO harbour model', 'story': 'DEMO fiction: the paper boat arrives.'},
                  {'name': 'DEMO paper craft history', 'story': 'DEMO fiction: visitors fold clouds into letters.'}],
        photo_angle='DEMO: frontal eye-level foyer composition; keep the exit clear; no flash.',
        photo_time='DEMO scenario 11:00, indoors; no claim about natural light',
        toilet_fee={'amount': 0, 'currency': currency}, floor='DEMO ground floor', tissue=True)
    pois[2].update(
        story='DEMO fiction: the workshop turns the imaginary cloud letters into bookmarks.',
        materials=['DEMO supplied paper', 'DEMO printed folding guide', 'DEMO supplied stamps'],
        products=[{'name': 'DEMO woven card sleeve', 'price': {'amount': 18, 'currency': currency},
                   'recipient_tags': ['craft', '手作']},
                  {'name': 'DEMO folded-paper bookmark', 'price': {'amount': 12, 'currency': currency},
                   'recipient_tags': ['reading', '讀書', '阅读', '閱讀', 'books']}])
    content = [
        {**source, 'id': f'demo-{code}-menu', 'poi_id': pois[0]['id'], 'kind': 'hidden_menu',
         'text': 'DEMO-only off-menu tea pairing; ask staff to confirm ingredients and availability.'},
        {**source, 'id': f'demo-{code}-diy', 'poi_id': pois[2]['id'], 'kind': 'diy',
         'text': 'DEMO paper bookmark workshop', 'materials': deepcopy(pois[2]['materials']), 'steps': ['Choose supplied paper', 'Fold along printed guides', 'Decorate with supplied stamps']},
        {**source, 'id': f'demo-{code}-safety', 'poi_id': pois[0]['id'], 'kind': 'safety',
         'text': 'DEMO rain scenario: avoid the slippery outdoor display and use the staffed indoor waiting area.'},
    ]
    events = [{**source, 'id': f'demo-{code}-event', 'poi_id': pois[1]['id'], 'public': True,
               'title': 'DEMO fictional public paper-story meetup', 'start_time': '2026-09-10T14:00:00+08:00'}]
    weather = {**source, 'id': f'demo-{code}-weather', 'available': True,
               'adverse': True, 'max_rain_probability': 80, 'temperature_max': 28,
               'forecast_for': '2026-09-10', 'description': 'DEMO rain scenario, not a forecast'}
    stops, nodes = [], {}
    for i, poi in enumerate(pois):
        stop = deepcopy(poi)
        stop.update(arrival_time=f'{10+i:02d}:00', end_time=f'{10+i:02d}:40')
        stops.append(stop)
        node_id = f'n{i+1}'
        nodes[node_id] = {'place': poi['name'], 'poi_id': poi['id'], 'poi': deepcopy(stop),
                          'type': 'attraction', 'optional': True, 'arrival_transport': 'DEMO unspecified',
                          'arrival_time': stop['arrival_time'], 'end_time': stop['end_time'],
                          'next_id': f'n{i+2}' if i < len(pois)-1 else None}
    trip = {'trip_id': f'demo-{code}', 'flights': [], 'hotels': [], 'weather_alerts': [],
            'days': {'2026-09-10': {'date': '2026-09-10', 'head_id': 'n1', 'nodes': nodes}}}
    nearby = {'request': {'city': city, 'date': '2026-09-10', 'mode': 'walking'},
              'stops': stops, 'legs': [], 'weather': deepcopy(weather), 'currency': currency,
              'schedule_verified': False, 'travel_times_available': False, 'demo': True,
              'reminders': ['Fictional example only; routes are not available.']}
    return {'city': city, 'mode': 'demo', 'members': [], 'pois': pois, 'content': content,
            'events': events, 'weather': weather, 'itinerary': {'trip_plan': trip, 'nearby_plan': nearby, 'city': city}}
