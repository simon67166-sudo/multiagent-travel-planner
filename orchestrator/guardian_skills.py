"""Deterministic, evidence-backed Travel Guardian workflows (no implicit LLM/network).

POIs come from adapters or guardian_demo. Supported optional evidence fields: diet
(list of supported restrictions), price ({amount,currency} or number + currency),
walking_distance_m (known walking lower bound to a candidate), accessibility bool,
soft affinities, indoor, queue_minutes, crowd_level, toilet, products, exhibits,
photo_spots. Content has kind, poi_id, text/steps; public events have poi_id,
title/start_time. Source records need source and ISO fetched_at; demo/data_kind
labels are preserved and never promoted to real evidence.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math
import re

if __package__:
    from .guardian_sources import content_is_current
else:
    from guardian_sources import content_is_current

SKILL_IDS = ('food_risk', 'group_consensus', 'queue', 'weather', 'toilet', 'souvenir',
             'citywalk', 'crowd', 'safety', 'social', 'hidden_menu', 'diy', 'lazy', 'museum', 'photo')
KEYWORDS = dict(zip(SKILL_IDS, [
    ('food', 'allerg', 'diet', '吃', '过敏', '過敏', '素食', '清真'),
    ('group', 'consensus', '一起', '同行', '团队', '團隊'),
    ('queue', 'wait', '排队', '排隊'), ('weather', 'rain', '天气', '天氣', '下雨', '台风', '颱風'),
    ('toilet', 'restroom', '厕所', '廁所', '洗手间', '洗手間'),
    ('souvenir', 'gift', '伴手礼', '伴手禮', '手信'), ('citywalk', 'city walk', '散步', '漫步'),
    ('crowd', '拥挤', '擁擠', '人多'), ('safety', 'safe', '安全', '危险', '危險'),
    ('social', 'meetup', '社交', '交友', '問路', '问路', '點餐', '点餐', '社恐'), ('hidden menu', '隐藏菜单', '隱藏菜單'),
    ('diy', 'craft', '手作', '手工'), ('lazy', 'tired', '累', '休息', '躺平'),
    ('museum', '博物馆', '博物館', '展览', '展覽'), ('photo', '摄影', '攝影', '拍照', '打卡')]))
ROLES = dict(zip(SKILL_IDS, ('FoodShopping','Group','Mobility','Guardian','Mobility','FoodShopping','Mobility','Mobility','Guardian','Experience','FoodShopping','Experience','Group','Experience','Experience')))
SKILLS = [{'id': skill, 'name': name, 'agent': ROLES[skill]} for skill, name in zip(SKILL_IDS,
    ('飲食風險', '團隊共識', '排隊調整', '天氣應變', '洗手間', '手信', '城市漫步', '人潮避讓', '安全提醒', '社交話術', '隱藏菜單', '手作體驗', '輕鬆行程', '博物館', '攝影'))]

DIET_ALIASES = {'素食': 'vegetarian', '纯素': 'vegan', '純素': 'vegan', '清真': 'halal',
                '无麸质': 'gluten_free', '無麩質': 'gluten_free', 'gluten-free': 'gluten_free',
                'no peanuts': 'peanut_free', '花生过敏': 'peanut_free', '花生過敏': 'peanut_free'}
CITY_ALIASES = {'hk': '香港', 'hong kong': '香港', 'macau': '澳門', 'macao': '澳門', '澳门': '澳門'}


def _number(value, label, maximum=None):
    try: finite = math.isfinite(value)
    except (OverflowError, TypeError): finite = False
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not finite or value < 0:
        raise ValueError(f'{label} must be a finite non-negative number')
    if maximum is not None and value > maximum:
        raise ValueError(f'{label} must be at most {maximum}')
    return float(value)


def _diet(value):
    if isinstance(value, str):
        value = re.split(r'[,;，；、]', value)
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ValueError('diet must be a string or list of strings')
    return sorted({DIET_ALIASES.get(v.strip().lower(), v.strip().lower()) for v in value if v.strip()})


def validate_preferences(data: dict) -> dict:
    """Return a new canonical dict; invalid input raises ValueError (never silently relax).

    Omitted diet=[], budget=None, walking_limit_m=None, accessibility=False;
    soft photo/culture/shopping/rest each default to neutral 0.5. Extra keys ignored.
    Budget amount >=0 is a PER-PERSON ENTIRE ITINERARY maximum, not per-item;
    explicit three-letter currency required, never infer/convert HKD/MOP.
    walking_limit_m >=0 is the ENTIRE itinerary's walking maximum, including transit
    connections and venue-internal walking. Numeric values must be finite, not bool.
    Diet accepts list[str] or comma/semicolon-separated string; unknown restrictions
    retained literally. Accessibility accepts bool or 'true'/'false'. Soft values
    must be in [0,1], not clamped. None instead of a dict is invalid.
    """
    if not isinstance(data, dict):
        raise ValueError('preferences must be a dict')
    budget = data.get('budget')
    if budget is not None:
        if not isinstance(budget, dict):
            raise ValueError('budget must contain amount and currency')
        currency = budget.get('currency')
        if not isinstance(currency, str) or not re.fullmatch('[A-Za-z]{3}', currency.strip()):
            raise ValueError('budget requires explicit three-letter currency')
        budget = {'amount': _number(budget.get('amount'), 'budget.amount'), 'currency': currency.strip().upper()}
    walking = data.get('walking_limit_m')
    if walking is not None:
        walking = _number(walking, 'walking_limit_m')
    access = data.get('accessibility', False)
    if isinstance(access, str) and access.lower().strip() in ('true', 'false'):
        access = access.lower().strip() == 'true'
    if not isinstance(access, bool):
        raise ValueError('accessibility must be boolean')
    soft = data.get('soft', {})
    if not isinstance(soft, dict):
        raise ValueError('soft must be a dict')
    return {'diet': _diet(data.get('diet', [])), 'budget': budget, 'walking_limit_m': walking,
            'accessibility': access, 'soft': {k: _number(soft.get(k, 0.5), f'soft.{k}', 1) for k in ('photo', 'culture', 'shopping', 'rest')}}


def _members(members):
    if not isinstance(members, list):
        raise ValueError('members must be a list')
    result = []
    for i, member in enumerate(members):
        if not isinstance(member, dict):
            raise ValueError(f'member {i} must be a dict')
        result.append({'id': member.get('id', str(i)), 'preferences': validate_preferences(member.get('preferences', {}))})
    return result


def _constraints(members):
    budgets = {}
    for member in members:
        budget = member['preferences']['budget']
        if budget:
            code = budget['currency']
            budgets[code] = min(budgets.get(code, math.inf), budget['amount'])
    limits = [m['preferences']['walking_limit_m'] for m in members if m['preferences']['walking_limit_m'] is not None]
    return {'diet': sorted({d for m in members for d in m['preferences']['diet']}),
            'budget_by_currency': budgets, 'budget_scope': 'per_person_entire_itinerary',
            'walking_limit_m': min(limits) if limits else None,
            'accessibility': any(m['preferences']['accessibility'] for m in members), 'member_count': len(members)}


def _price(record):
    value = record.get('price')
    if type(value) in (int, float):
        value = {'amount': value, 'currency': record.get('currency')}
    try:
        return validate_preferences({'budget': value})['budget']
    except ValueError:
        return None


def _hard_check(poi, hard, check_walking=True):
    reasons, unknowns = [], []
    if poi.get('_fact_conflicts'):
        reasons.append('conflicting sourced facts: ' + ', '.join(poi['_fact_conflicts']))
    category = str(poi.get('category', '')).lower()
    nonfood = any(k in category for k in ('museum','toilet','shop','park','博物','廁所','公園','景點')) and not any(k in category for k in ('restaurant','cafe','food','餐'))
    if hard['diet'] and not nonfood:
        if poi.get('diet') is None:
            unknowns.append('diet suitability')
        else:
            try:
                supported = set(_diet(poi['diet']))
                if 'vegan' in supported:
                    supported.add('vegetarian')
                if not set(hard['diet']) <= supported:
                    reasons.append('diet restriction not supported')
            except ValueError:
                unknowns.append('invalid diet evidence')
    budgets = hard['budget_by_currency']
    if budgets:
        price = _price(poi)
        if price is None:
            unknowns.append('price and currency')
        elif set(budgets) != {price['currency']}:
            reasons.append('currency mismatch; no FX conversion available')
        elif price['amount'] > budgets[price['currency']]:
            reasons.append('maximum budget exceeded')
    if check_walking and hard['walking_limit_m'] is not None:
        try:
            distance = _number(poi.get('walking_distance_m'), 'walking_distance_m')
        except ValueError:
            unknowns.append('walking distance')
        else:
            if distance > hard['walking_limit_m']:
                reasons.append('walking maximum exceeded')
    if hard['accessibility']:
        if poi.get('accessibility') is False:
            reasons.append('accessibility unavailable')
        elif poi.get('accessibility') is not True:
            unknowns.append('accessibility')
    return reasons, unknowns


def _rank(poi, members):
    affinities = poi.get('soft', {})
    affinities = affinities if isinstance(affinities, dict) else {}
    scores = []
    for member in members:
        prefs = member['preferences']['soft']
        known = [(prefs[k], v) for k, v in affinities.items() if k in prefs and type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1]
        total = sum(w for w, _ in known)
        score = sum(w * v for w, v in known) / total if total else 0.5
        scores.append({'member_id': member['id'], 'score': score})
    average = sum(x['score'] for x in scores) / len(scores) if scores else 0.5
    minimum = min((x['score'] for x in scores), default=0.5)
    return {**deepcopy(poi), 'member_scores': scores, 'average_score': average, 'minimum_score': minimum,
            'score': (average + minimum) / 2, 'preference_evidence_available': bool(affinities)}


def evaluate_candidates(pois: list, members: list) -> dict:
    """Pure pre-route hard filter. eligible/unknown/excluded contain COPIES of POIs.

    Each adds reasons:list[str], unknowns:list[str], fairness average/minimum/score
    and member_scores. 'eligible' means only individual-POI checks passed, NOT
    cumulative itinerary feasibility. Unknowns never enter eligible. Provenance
    validation is the caller's responsibility (run_skills validates it). Invalid
    preferences/collection types raise ValueError rather than dropping restrictions.
    Numeric adapter price+currency and nested price are both supported.
    """
    if not isinstance(pois, list) or any(not isinstance(p, dict) for p in pois):
        raise ValueError('pois must be a list of dicts')
    normalized = _members(members)
    hard = _constraints(normalized)
    output = {'eligible': [], 'unknown': [], 'excluded': [], 'constraint_summary': hard}
    for poi in pois:
        reasons, unknowns = _hard_check(poi, hard)
        row = _rank(poi, normalized)
        row.update(reasons=reasons, unknowns=list(dict.fromkeys(poi.get('unknowns', []) + unknowns)) if isinstance(poi.get('unknowns', []), list) else unknowns,
                   hard_unknowns=unknowns, individual_constraints_verified=not reasons and not unknowns)
        output['excluded' if reasons else 'unknown' if unknowns else 'eligible'].append(row)
    for key in ('eligible', 'unknown', 'excluded'):
        output[key].sort(key=lambda p: (-p['score'], str(p.get('id', ''))))
    return output


def _ordered(day):
    nodes = day.get('nodes', {})
    order, seen, current = [], set(), day.get('head_id')
    while current is not None:
        if current in seen or current not in nodes:
            raise ValueError('invalid legacy itinerary chain')
        seen.add(current)
        order.append(current)
        current = nodes[current].get('next_id')
    if seen != set(nodes):
        raise ValueError('unreachable legacy itinerary nodes')
    return order


def validate_itinerary_feasibility(itinerary: dict, members: list) -> dict:
    """Validate complete canonical itinerary without mutation; no currency conversion.

    Returns status='feasible'|'unknown'|'infeasible', feasible:bool, violations,
    unknowns, total_cost_by_currency, known_walking_m, constraint_summary.
    nearby_plan.stops/legs replace only its request date; legacy POIs on other
    days and their day-level legs remain in the total. Other-day missing coverage
    stays unknown. Without nearby, use legacy nodes' poi + trip_plan.legs. Prices are per-person; legs need explicit price
    even when zero. Hotels/flights/additional_costs are included (same price shape).
    Internal walking requires internal_walking_m per stop. Walking legs distance_m,
    transit/driving walking_distance_m include connections. Missing/unavailable legs,
    missing prices, cost_scope != per_person, or cost_complete/walking_complete !=
    True remain unknown. Completeness flags are adapter assertions, not inferred.
    Known subtotal violations are reported even with other missing data. No known
    violation is classified as merely unknown. Acceptance may permit unknown only
    through an explicit integration policy; feasible is true only with no unknowns.
    """
    if not isinstance(itinerary, dict):
        raise ValueError('itinerary must be a dict')
    hard = _constraints(_members(members))
    violations, unknowns = [], []
    trip = itinerary.get('trip_plan') or {}
    nearby = itinerary.get('nearby_plan')
    plan = nearby if isinstance(nearby, dict) else trip
    other_days = []
    if isinstance(nearby, dict):
        raw_stops = nearby.get('stops', [])
        if not isinstance(raw_stops, list):
            raise ValueError('stops must be a list')
        stops = deepcopy(raw_stops)
        nearby_date = (nearby.get('request') or {}).get('date')
        for day_key, day in trip.get('days', {}).items():
            if str(day_key) == str(nearby_date) or (nearby_date is not None and str(day.get('date')) == str(nearby_date)):
                continue
            # Undated legacy mirrors cannot safely be resolved by POI name/ID.
            if nearby_date is None:
                unknowns.append('nearby request date missing; cross-day overlap unverified')
            other_days.append(day)
            for key in _ordered(day):
                node = day['nodes'][key]
                stops.append(deepcopy(node.get('poi', node)))
    else:
        stops = []
        for day in trip.get('days', {}).values():
            for key in _ordered(day):
                node = day['nodes'][key]
                stops.append(deepcopy(node.get('poi', node)))
    legs = deepcopy(plan.get('legs', []))
    if not isinstance(legs, list):
        raise ValueError('legs must be a list')
    extra_costs = deepcopy(plan.get('additional_costs', []))
    for day in other_days:
        day_legs = day.get('legs', [])
        if not isinstance(day_legs, list):
            raise ValueError('day legs must be a list')
        legs.extend(deepcopy(day_legs))
        extra_costs.extend(deepcopy(day.get('additional_costs', [])))
        count = len(day.get('nodes', {}))
        if count and len(day_legs) < count:
            unknowns.append('other-day origin/connecting legs and transport costs unknown')
        if count and day.get('cost_complete') is not True:
            unknowns.append('other-day cost coverage unverified')
        if count and day.get('walking_complete') is not True:
            unknowns.append('other-day walking coverage unverified')
    for record in stops + legs + extra_costs:
        _refresh_joined_facts(record, 'demo' if record.get('demo') or record.get('data_kind') == 'demo' else 'real')
    costs, walking = {}, 0.0
    for stop in stops:
        reasons, missing = _hard_check(stop, hard, check_walking=False)
        violations.extend(f"{stop.get('id', stop.get('place', 'stop'))}: {r}" for r in reasons)
        unknowns.extend(f"{stop.get('id', 'stop')}: {r}" for r in missing)
        if hard['walking_limit_m'] is not None:
            try:
                walking += _number(stop.get('internal_walking_m'), 'internal walking')
            except ValueError:
                unknowns.append('venue internal walking unknown')
    if hard['walking_limit_m'] is not None:
        if len(legs) < len(stops):
            unknowns.append('missing origin/connecting walking legs')
        for leg in legs:
            if leg.get('available') is not True:
                unknowns.append('route unavailable')
                continue
            field = 'distance_m' if leg.get('mode') == 'walking' else 'walking_distance_m'
            try:
                walking += _number(leg.get(field), field)
            except ValueError:
                unknowns.append('route walking distance unknown')
        if walking > hard['walking_limit_m']:
            violations.append('total walking maximum exceeded')
        if plan.get('walking_complete') is not True:
            unknowns.append('whole-itinerary walking coverage unverified')
    cost_records = stops + legs + trip.get('hotels', []) + trip.get('flights', []) + extra_costs
    if hard['budget_by_currency']:
        for record in cost_records:
            price = _price(record)
            if not price:
                unknowns.append('itinerary cost item missing price/currency')
                continue
            if record.get('cost_scope', 'per_person') != 'per_person':
                unknowns.append('cost allocation per person unknown')
                continue
            code = price['currency']
            costs[code] = costs.get(code, 0.0) + price['amount']
        for code, total in costs.items():
            if set(hard['budget_by_currency']) != {code}:
                violations.append('itinerary currency mismatch; no FX conversion available')
            if code in hard['budget_by_currency'] and total > hard['budget_by_currency'][code]:
                violations.append('total per-person itinerary budget exceeded')
        if plan.get('cost_complete') is not True:
            unknowns.append('whole-itinerary cost coverage unverified')
    if hard['accessibility']:
        if len(legs) < len(stops):
            unknowns.append('route accessibility unknown')
        for leg in legs:
            if leg.get('accessibility') is False:
                violations.append('route accessibility unavailable')
            elif leg.get('accessibility') is not True:
                unknowns.append('route accessibility unknown')
    return {'status': 'infeasible' if violations else 'unknown' if unknowns else 'feasible',
            'feasible': not violations and not unknowns, 'violations': list(dict.fromkeys(violations)),
            'unknowns': list(dict.fromkeys(unknowns)), 'total_cost_by_currency': costs,
            'known_walking_m': walking, 'constraint_summary': hard}


def _provenance(record, mode):
    if not isinstance(record, dict):
        return None
    demo = bool(record.get('demo')) or record.get('data_kind') == 'demo' or str(record.get('source', '')).upper().startswith('DEMO')
    if mode == 'real' and demo:
        return None
    source, stamp = record.get('source'), record.get('fetched_at')
    if not isinstance(source, str) or not source.strip() or not isinstance(stamp, str):
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
    except ValueError:
        return None
    if not content_is_current(record):
        return None
    return {'source': source, 'source_url': record.get('source_url'), 'fetched_at': stamp,
            'demo': demo, 'data_kind': record.get('data_kind', 'demo' if demo else 'real'),
            'record_id': record.get('id', record.get('poi_id')),
            **{k: record[k] for k in ('valid_from', 'valid_until') if k in record}}


def _schedule(itinerary, skill, candidates):
    """Compose a provisional schedule, updating both legacy and nearby representations."""
    proposal = deepcopy(itinerary)
    changes = []
    by_id = {str(p.get('id')): p for p in candidates}
    by_name = {p.get('name'): p for p in candidates}
    def match(stop):
        data = stop.get('poi') if isinstance(stop.get('poi'), dict) else stop
        return by_id.get(str(data.get('poi_id', data.get('id')))) or by_name.get(data.get('place', data.get('name')))
    def arrange(stops):
        if skill == 'lazy':
            kept, removed = [], []
            for stop in stops:
                data = stop.get('poi') if isinstance(stop.get('poi'), dict) else stop
                if kept and data.get('optional', stop.get('type') == 'attraction') is True and not stop.get('booking_ref') and not data.get('booking_ref') and stop.get('type') != 'meal':
                    removed.append(stop)
                else:
                    kept.append(stop)
            return kept, removed
        field = {'queue': 'queue_minutes', 'crowd': 'crowd_level', 'weather': 'indoor'}[skill]
        result, segment = list(stops), []
        def flush():
            ordered = sorted((result[i] for i in segment), key=lambda s: (not match(s)[field]) if skill == 'weather' else match(s)[field])
            for i, stop in zip(segment, ordered):
                result[i] = stop
            segment.clear()
        for i, stop in enumerate(stops):
            poi = match(stop)
            if poi is None or field not in poi or stop.get('booking_ref') or poi.get('booking_ref') or stop.get('type') == 'meal':
                flush()
            else:
                segment.append(i)
        flush()
        return result, []
    days = proposal.get('trip_plan', {}).get('days', {})
    if not isinstance(days, dict):
        raise ValueError('trip_plan.days must use legacy linked-days format')
    for date, day in days.items():
        order = _ordered(day)
        stops = [dict(day['nodes'][key], _guardian_node_id=key) for key in order]
        arranged, removed = arrange(stops)
        new_order = [s['_guardian_node_id'] for s in arranged]
        if new_order == order:
            continue
        changes.append({'day': date, 'before': order, 'after': new_order, 'deferred': [s['_guardian_node_id'] for s in removed]})
        day.setdefault('guardian_deferred', []).extend({k: v for k, v in s.items() if k != '_guardian_node_id'} for s in removed)
        day['head_id'] = new_order[0] if new_order else None
        day['nodes'] = {key: day['nodes'][key] for key in new_order}
        for i, key in enumerate(new_order):
            node = day['nodes'][key]
            node['next_id'] = new_order[i + 1] if i + 1 < len(new_order) else None
            if not node.get('booking_ref'):
                node.update(arrival_time='待確認', end_time='待確認')
            node['arrival_transport'] = '待確認'
        day['schedule_verified'] = False
    nearby = proposal.get('nearby_plan')
    if isinstance(nearby, dict) and isinstance(nearby.get('stops'), list):
        arranged, removed = arrange(nearby['stops'])
        if arranged != nearby['stops']:
            changes.append({'nearby': True, 'before': [s.get('id') for s in nearby['stops']], 'after': [s.get('id') for s in arranged]})
            nearby['stops'] = arranged
            nearby.setdefault('guardian_deferred', []).extend(removed)
            nearby['legs'] = []
            nearby.update(schedule_verified=False, travel_times_available=False, walking_complete=False, cost_complete=False)
            for stop in arranged:
                if not stop.get('booking_ref'):
                    stop.update(arrival_time='待確認', end_time='待確認')
        elif changes:
            # Nearby is authoritative during canonical-store acceptance. Never let an
            # unchanged nearby representation undo changes to a different legacy plan.
            proposal['nearby_plan'] = None
    if changes:
        trip = proposal.get('trip_plan', {})
        trip.update(legs=[], walking_complete=False, cost_complete=False)
    return (proposal if changes else None), changes


def _numeric(record, key):
    value = record.get(key)
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _adverse(weather):
    if not weather:
        return False
    if weather.get('adverse') is True or (_numeric(weather, 'max_rain_probability') and weather['max_rain_probability'] >= 60) or (_numeric(weather, 'temperature_max') and weather['temperature_max'] >= 32):
        return True
    # Amap daily/observed conditions stay daily/observed, never invented hourly slots.
    descriptions = []
    records = list(weather.get('records') or [])
    for section in ('live', 'forecast'):
        part = weather.get(section)
        if isinstance(part, dict):
            records.extend(part.get('records') or [])
    for record in records:
        if not isinstance(record, dict):
            continue
        descriptions.append(str(record.get('weather', '')))
        for cast in record.get('casts') or []:
            if isinstance(cast, dict):
                descriptions.extend(str(cast.get(k, '')) for k in ('dayweather', 'nightweather'))
    return any(word in ' '.join(descriptions).lower() for word in ('雨', '雷', '雪', '台风', '颱風', 'rain', 'storm', 'typhoon'))



CONTENT_FIELDS = {'diet', 'accessibility', 'walking_distance_m', 'internal_walking_m',
                  'queue_minutes', 'crowd_level', 'indoor', 'toilet', 'soft', 'products',
                  'exhibits', 'photo_spots', 'cross_contact', 'opening_hours', 'price', 'currency',
                  'story', 'stories', 'ordered_exhibits', 'photo_angle', 'photo_time',
                  'materials', 'toilet_fee', 'floor', 'tissue'}


def _refresh_joined_facts(poi, mode):
    """Persist field provenance and recheck it when candidates are reused."""
    ledger = poi.get('field_evidence', {})
    if not isinstance(ledger, dict):
        poi.pop('field_evidence', None)
        return
    current = {}
    for key, entry in ledger.items():
        if key not in CONTENT_FIELDS or not isinstance(entry, dict):
            continue
        evidence = [e for e in entry.get('evidence', []) if _provenance(e, mode)]
        if evidence:
            current[key] = {**entry, 'evidence': evidence}
            poi.setdefault('_supporting_evidence', []).extend(evidence)
        elif poi.get(key) == entry.get('value'):
            poi.pop(key, None)
    poi['field_evidence'] = current


def _field_sources(poi, field):
    return deepcopy(poi.get('field_evidence', {}).get(field, {}).get('evidence', [poi.get('_evidence')]))


def _attach_content(pois, content):
    """Join exactly one matching ID; no names, coordinates or source replacement."""
    normalized = []
    for record in content:
        facts = deepcopy(record.get('facts')) if isinstance(record.get('facts'), dict) else {}
        item = {**record, 'kind': record.get('kind', facts.get('kind', record.get('category'))),
                'text': record.get('text', facts.get('text')),
                'steps': deepcopy(record.get('steps', facts.get('steps', [])))}
        if item['kind'] in ('museum', 'citywalk') and item.get('text') and not facts.get('story'):
            facts['story'] = item['text']
        linked = [p for p in pois if (record.get('poi_id') or record.get('amap_id'))
                  and (not record.get('poi_id') or record['poi_id'] == p['id'])
                  and (not record.get('amap_id') or record['amap_id'] == p.get('amap_id'))
                  and record['_evidence']['demo'] == p['_evidence']['demo']]
        if len(linked) != 1:
            normalized.append({**item, 'poi_id': None, 'linked': False})
            continue
        poi = linked[0]
        for key in CONTENT_FIELDS:
            if key not in facts or facts[key] is None:
                continue
            if poi.get(key) is None:
                poi[key] = deepcopy(facts[key])
                poi.setdefault('field_evidence', {})[key] = {
                    'value': deepcopy(facts[key]), 'evidence': [record['_evidence']]}
            elif poi[key] != facts[key]:
                poi.setdefault('_fact_conflicts', []).append(key)
            elif key in poi.get('field_evidence', {}):
                poi['field_evidence'][key]['evidence'].append(record['_evidence'])
        poi.setdefault('_supporting_evidence', []).append(record['_evidence'])
        normalized.append({**item, 'poi_id': poi['id'], 'linked': True})
    return normalized


def _text_input(value):
    return value.strip() if isinstance(value, str) else ''


def _social_action(message, context, hard):
    social = context.get('social')
    social = social if isinstance(social, dict) else {}
    order = _text_input(social.get('order', context.get('order')))
    destination = _text_input(social.get('destination', context.get('destination')))
    if not order:
        match = re.search(r'(?:想點|想点|想吃|order\s+)([^，。；;!?！？\n]+)', message, re.I)
        order = match.group(1).strip() if match else ''
    if not destination:
        match = re.search(r'(?:怎麼去|怎么去|前往|去|directions to\s+|way to\s+)([^，。；;!?！？\n]+)', message, re.I)
        destination = match.group(1).strip() if match else ''
    intents, scripts = [], []
    if order or any(x in message.lower() for x in ('order', '點餐', '点餐', '吃')):
        intents.append('ordering')
        scripts.append('你好，我想點：' + (order or '請按下方原話確認餐點') + '。請先確認材料與供應情況。')
    directions = '請問前往「' + destination + '」應該怎麼走？請指出可走的公共路線。' if destination else '請問可以協助確認我要去的目的地與路線嗎？'
    if destination or any(x in message.lower() for x in ('direction', '問路', '问路')):
        intents.append('directions')
        scripts.append(directions)
    allergies = social.get('allergies', context.get('allergies', []))
    allergies = [allergies] if isinstance(allergies, str) else allergies
    restrictions = list(hard['diet'])
    if isinstance(allergies, list):
        restrictions.extend(x.strip() for x in allergies if isinstance(x, str) and x.strip())
    restrictions.extend(x.strip() for x in re.split(r'[，。；;!?！？\n]', message)
                        if any(k in x.lower() for k in ('allerg', '過敏', '过敏')))
    restrictions = list(dict.fromkeys(restrictions))
    if restrictions:
        intents.append('allergies')
        scripts.append('飲食限制／過敏原話：' + '；'.join(restrictions) + '。請向廚房確認材料、醬料及共用器具；不能確認請告訴我，先不要下單。')
    scripts.append('我的原話：' + (message or '請協助我溝通，並先確認需求。'))
    ready = '\n'.join(scripts)
    return {'ready_to_show': ready, 'user_message': message, 'intents': intents,
            'order': order or None, 'destination': destination or None,
            'dietary_requirements': restrictions, 'directions_script': directions,
            'dialogue': [{'speaker': '旅客', 'text': ready},
                         {'speaker': '旅客（確認追問）', 'text': '請確認後再告訴我；不確定的部分請明確說明。'}],
            'decision': 'user-worded communication aid; no staff answer or route asserted'}


def _recipient(message, context):
    value = context.get('recipient', context.get('souvenir_recipient'))
    return deepcopy(value) if isinstance(value, (str, dict)) and value else message.strip() or '請補充收禮人的興趣'


def _gift_products(products, recipient, poi):
    terms, result = str(recipient).lower(), []
    for product in products:
        item = deepcopy(product) if isinstance(product, dict) else {'name': str(product)}
        tags = item.get('recipient_tags', [])
        tags = tags if isinstance(tags, list) else []
        item['matched_interests'] = [t for t in tags if isinstance(t, str) and t.lower() in terms]
        item['price'] = _price(item)
        item['price_source'] = _field_sources(poi, 'products') if item['price'] else []
        item['fit_basis'] = 'explicit product tags only; no recipient traits inferred'
        result.append(item)
    return sorted(result, key=lambda p: -len(p['matched_interests']))


def _workflow(skill, pois, content, events, weather, row, message, context):
    """Skill-specific grounded transformations; actions carry executable decisions."""
    selected = []
    if skill == 'food_risk':
        selected = [p for p in pois if any(t in str(p.get('category', '')).lower() for t in ('restaurant', 'cafe', 'food', '餐', '咖啡'))]
        row['actions'] = [{'poi_id': p['id'], 'supported_diet': p.get('diet'), 'cross_contact': p.get('cross_contact', 'unknown'), 'decision': 'verify kitchen cross-contact before ordering'} for p in selected]
        if any(p.get('cross_contact') is None for p in selected):
            row['unknowns'].append('kitchen cross-contact not verified')
    elif skill == 'group_consensus':
        selected = pois
        row['actions'] = [{'rank': i + 1, 'poi_id': p['id'], 'average': p['average_score'], 'minimum': p['minimum_score'], 'fairness': p['score']} for i, p in enumerate(pois)]
        if any(not p['preference_evidence_available'] for p in selected):
            row['unknowns'].append('missing affinity attributes; neutral score is not evidence of preference fit')
    elif skill in ('queue', 'crowd'):
        field = 'queue_minutes' if skill == 'queue' else 'crowd_level'
        fresh=[]
        for p in pois:
            is_demo=p.get('demo') or p.get('data_kind')=='demo'
            age = max((datetime.now(timezone.utc)-datetime.fromisoformat(e['fetched_at'].replace('Z','+00:00'))).total_seconds() for e in _field_sources(p, field))
            if is_demo or 0 <= age <= 3600: fresh.append(p)
            elif _numeric(p,field): row['unknowns'].append('排隊／人潮紀錄已過期，請重新確認')
        selected = sorted((p for p in fresh if _numeric(p, field)), key=lambda p: (p[field], -p['score'], str(p['id'])))
        row['actions'] = [{'poi_id': p['id'], field: p[field], 'rank': i + 1, 'decision': 'prefer lower observed load; snapshot not a live guarantee'} for i, p in enumerate(selected)]
    elif skill == 'weather':
        selected = sorted((p for p in pois if isinstance(p.get('indoor'), bool)), key=lambda p: (not p['indoor'], -p['score'])) if weather else []
        row['actions'] = [{'weather': deepcopy(weather), 'adverse': _adverse(weather), 'indoor_alternatives': [p['id'] for p in selected if p['indoor']], 'decision': 'sheltered visits first' if _adverse(weather) else 'retain current schedule'}] if weather else []
    elif skill == 'toilet':
        selected = sorted((p for p in pois if p.get('toilet') is True), key=lambda p: p['walking_distance_m'] if _numeric(p, 'walking_distance_m') else math.inf)
        row['actions'] = [{'poi_id': p['id'], 'accessibility': p.get('accessibility'), 'opening_hours': p.get('opening_hours'), 'rank': i + 1} for i, p in enumerate(selected)]
    elif skill in ('souvenir', 'museum', 'photo'):
        field = {'souvenir': 'products', 'museum': 'exhibits', 'photo': 'photo_spots'}[skill]
        selected = [p for p in pois if (isinstance(p.get(field), list) and p[field]) or (skill == 'museum' and (p.get('story') or p.get('ordered_exhibits')))]
        row['actions'] = [{'poi_id': p['id'], field: deepcopy(p.get(field, [])), 'decision': {'souvenir': 'compare listed products; purchases require separate total-budget check', 'museum': 'follow exhibit order; confirm tickets and opening hours', 'photo': 'use listed viewpoints and photography rules'}[skill]} for p in selected]
    elif skill == 'citywalk':
        selected = sorted((p for p in pois if _numeric(p, 'walking_distance_m')), key=lambda p: (p['walking_distance_m'], -p['score']))
        row['actions'] = [{'poi_id': p['id'], 'known_walking_m': p['walking_distance_m'], 'rank': i + 1, 'decision': 'single-destination walking alternative; obtain actual route before combining'} for i, p in enumerate(selected)]
    elif skill == 'social':
        row['actions'] = [_social_action(message, context, row['constraint_summary'])]
        public = [e for e in events if e.get('public') is True and e.get('title')]
        row['public_events'] = [{k: deepcopy(v) for k, v in e.items() if not k.startswith('_')} for e in public]
        row['evidence'].extend(e['_evidence'] for e in public)
        row['unknowns'].append('實際材料、供應、路線及對方回覆待確認')
    elif skill in ('hidden_menu', 'diy', 'safety'):
        ids = {p['id'] for p in pois}
        matches = [c for c in content if c.get('kind') == skill and c.get('poi_id') in ids and (c.get('text') or c.get('steps'))]
        selected = [p for p in pois if any(c['poi_id'] == p['id'] for c in matches)]
        row['actions'] = [{'poi_id': c['poi_id'], 'text': c.get('text'), 'steps': deepcopy(c.get('steps', [])), 'materials': deepcopy(c.get('materials', c.get('facts', {}).get('materials'))), 'content_source': c['_evidence'], 'decision': {'hidden_menu': 'ask venue to confirm sourced item; no availability guarantee', 'diy': 'follow supplied workshop steps; confirm materials and supervision', 'safety': 'apply sourced advisory; no safety guarantee'}[skill]} for c in matches]
        row['evidence'].extend(c['_evidence'] for c in matches)
    elif skill == 'lazy':
        selected = sorted(pois, key=lambda p: -(p.get('soft', {}).get('rest', 0.5) if isinstance(p.get('soft'), dict) and _numeric(p['soft'], 'rest') else 0.5))
        row['actions'] = [{'poi_id': p['id'], 'rank': i + 1, 'decision': 'rest option; defer optional later visits in existing itinerary'} for i, p in enumerate(selected)]
    for action in row['actions']:
        poi=next((p for p in selected if p.get('id')==action.get('poi_id')), {})
        if skill in ('queue','crowd'):
            action['alternatives']=[{'choice':'原地等待','note':'僅為目前紀錄，不保證到場等候時間'}, {'choice':'改去其他候選','poi_ids':[p['id'] for p in selected if p['id']!=poi.get('id')]}, {'choice':'先逛再回來','note':'先提出順序調整，回來前重新查詢'}]
        elif skill=='toilet':
            action.update(fee=poi.get('toilet_fee'),floor=poi.get('floor'),tissue=poi.get('tissue'))
            row['unknowns'].extend(k+' 未提供' for k in ('fee','floor','tissue') if action[k] is None)
        elif skill=='souvenir':
            recipient = _recipient(message, context)
            products = _gift_products(poi.get('products', []), recipient, poi)
            action.update(recipient=recipient, products=products, product_source=_field_sources(poi, 'products'),
                          tax='無已核實稅務資料，不估算退稅')
            if any(p['price'] is None for p in products):
                row['unknowns'].append('product price/currency unknown; venue price is not a gift price')
        elif skill=='museum':
            action.update(ordered_exhibits=deepcopy(poi.get('ordered_exhibits',poi.get('exhibits',[]))),story=poi.get('story','來源未提供展品故事'),fiction=bool(poi.get('demo') or poi.get('data_kind') == 'demo'),
                          story_source=_field_sources(poi, 'story'),
                          order_basis='explicit sourced order' if poi.get('ordered_exhibits') else 'source listing order only; no venue route asserted')
        elif skill=='photo':
            spots = [spot for spot in poi.get('photo_spots', []) if isinstance(spot, dict)]
            action.update(angle=poi.get('photo_angle') or [spot['angle'] for spot in spots if spot.get('angle')] or None,
                          time=poi.get('photo_time') or [spot['time'] for spot in spots if spot.get('time')] or None,
                          viewpoint_source=_field_sources(poi, 'photo_spots'))
            row['unknowns'].append('拍攝角度、最佳時間與現場規則以資料及現場確認為準')
        elif skill=='diy':
            action.update(price=poi.get('price'),materials=action.get('materials') or poi.get('materials'),availability='名額及可預訂狀態待確認')
        elif skill=='citywalk':
            action.update(ordered_stops=[p['id'] for p in selected],stories=[{'poi_id':p['id'],'text':p.get('story','來源未提供故事'),'fiction':bool(p.get('demo') or p.get('data_kind') == 'demo'), 'source':_field_sources(p, 'story'), 'stories':deepcopy(p.get('stories', []))} for p in selected])
    if skill == 'safety' and not row['actions']:
        row['actions'] = [{'scope': 'general', 'local_bulletin_available': False,
                           'text': '保管好隨身物品，使用開放的公共通道；遇到不確定情況向現場工作人員求助。',
                           'decision': 'general suggestions only; no local incident or crime assessment'}]
        row['unknowns'].append('no current sourced local safety bulletin')
    if not selected:
        row['unknowns'].append(f'{skill}: no verified matching records')
    row['evidence'].extend(p['_evidence'] for p in selected)
    for poi in selected:
        row['evidence'].extend(poi.get('_supporting_evidence', []))
    return selected


def run_skills(message: str, context: dict, requested_skills: list | None = None) -> dict:
    """Run allowlisted deterministic workflows; JSON-compatible result, no mutation.

    Default mode real, empty members/records, no itinerary. None selects multilingual
    keywords with group_consensus fallback; [] runs nothing. Unsupported IDs reported
    in conflicts. Invalid preferences fail closed. No implicit external/model calls.
    Source-less records and demo records in real mode excluded. Scheduling composes
    in requested order, returns ENTIRE canonical itinerary; caller persists it only
    after acceptance checks. Changed routes/times become unknown, never invented.
    context.weather optionally overrides nearby_plan.weather.
    """
    if not isinstance(message, str) or not isinstance(context, dict):
        raise ValueError('message must be str and context must be dict')
    if requested_skills is not None and not isinstance(requested_skills, list):
        raise ValueError('requested_skills must be list or None')
    mode = context.get('mode', 'real')
    if mode not in ('real', 'demo'):
        raise ValueError('mode must be real or demo')
    requested = requested_skills if requested_skills is not None else [s for s in SKILL_IDS if s in message.lower() or any(k in message.lower() for k in KEYWORDS[s])]
    if requested_skills is None and not requested:
        requested = ['group_consensus']
    selected, conflicts, errors, unknowns = [], [], [], []
    for skill in requested:
        if not isinstance(skill, str) or skill not in SKILL_IDS:
            conflicts.append({'skill': str(skill), 'reason': 'unsupported skill'})
        elif skill not in selected:
            selected.append(skill)
    try:
        members = _members(context.get('members', []))
    except ValueError as exc:
        errors.append(str(exc))
        members = []
    hard = _constraints(members)
    if len(hard['budget_by_currency']) > 1:
        conflicts.append({'reason': 'mixed member budget currencies; no FX conversion available'})
    def records(key):
        values = context.get(key, [])
        if not isinstance(values, list):
            unknowns.append(f'{key}: expected list')
            return []
        accepted = []
        for record in values:
            evidence = _provenance(record, mode)
            if evidence is None:
                unknowns.append(f'{key}: missing/invalid provenance, expired/not-yet-valid record, or demo excluded in real mode')
                continue
            item = {**deepcopy(record), '_evidence': evidence}
            if key == 'pois':
                _refresh_joined_facts(item, mode)
            accepted.append(item)
        return accepted
    pois, seen = [], set()
    city = CITY_ALIASES.get(str(context.get('city', '')).lower(), context.get('city'))
    for poi in records('pois'):
        if not isinstance(poi.get('id'), str) or not poi.get('name') or poi['id'] in seen:
            unknowns.append('POI requires unique string id and name')
            continue
        seen.add(poi['id'])
        poi_city = CITY_ALIASES.get(str(poi.get('city', '')).lower(), poi.get('city'))
        if poi_city and city and poi_city != city:
            conflicts.append({'poi_id': poi['id'], 'reason': 'different city'})
        else:
            pois.append(poi)
    content, events = records('content'), records('events')
    content = [c for c in content if not c.get('city') or not city or
               CITY_ALIASES.get(str(c['city']).lower(), c['city']) == city]
    content = _attach_content(pois, content)
    evaluated = evaluate_candidates(pois, members)
    candidates = evaluated['eligible'] if not errors else []
    for p in evaluated['excluded']:
        conflicts.extend({'poi_id': p['id'], 'reason': reason} for reason in p['reasons'])
    for p in evaluated['unknown'] + evaluated['excluded']:
        unknowns.extend(f"{p['id']}: {item}" for item in p['hard_unknowns'])
    working = deepcopy(context.get('itinerary')) if isinstance(context.get('itinerary'), dict) else None
    nearby = (working or {}).get('nearby_plan')
    weather = context.get('weather', nearby.get('weather') if isinstance(nearby, dict) else None)
    weather_evidence = _provenance(weather, mode)
    weather = weather if weather_evidence and weather.get('available', True) else None
    results, sources, proposal = [], [], None
    for skill in selected:
        row = {'skill': skill, 'agent': ROLES[skill], 'candidates': [], 'evidence': [],
               'limitations': ['Source snapshots are not live guarantees; verify venue access, cross-contact, opening hours and transport.', 'Candidates are alternatives, not a verified combined itinerary.'],
               'errors': list(errors) + deepcopy(context.get('errors', [])) if isinstance(context.get('errors', []), list) else list(errors), 'unknowns': list(unknowns), 'actions': [], 'constraint_summary': deepcopy(hard)}
        choices = _workflow(skill, candidates, content, events, weather, row, message, context)
        row['informational_items'] = [
            {k: deepcopy(v) for k, v in c.items() if not k.startswith('_')}
            for c in content if not c.get('linked') and c.get('kind') == skill]
        row['evidence'].extend(c['_evidence'] for c in content
                               if not c.get('linked') and c.get('kind') == skill)
        row['excluded']=[{k:v for k,v in p.items() if not k.startswith('_')} for p in evaluated['excluded']]
        if skill=='food_risk':
            row['evidence'].extend(p['_evidence'] for p in evaluated['excluded'] if p.get('_evidence'))
            for p in evaluated['excluded']:
                row['evidence'].extend(p.get('_supporting_evidence', []))
        row['candidates'] = [{k: v for k, v in p.items() if not k.startswith('_')} for p in choices]
        if not members:
            row['unknowns'].append('member preferences not supplied; neutral group score')
        if skill == 'weather' and weather:
            row['evidence'].append(weather_evidence)
        if skill in ('queue', 'crowd', 'weather', 'lazy'):
            if working and choices and not errors and (skill != 'weather' or _adverse(weather)):
                try:
                    updated, changes = _schedule(working, skill, choices)
                    row['schedule_changes'] = changes
                    if updated:
                        working = proposal = updated
                        row['unknowns'].append('changed schedule requires route/time/total-cost/total-walking revalidation')
                except (ValueError, TypeError, KeyError) as exc:
                    row['errors'].append(str(exc))
            elif not working:
                row['unknowns'].append('existing canonical itinerary unavailable')
        row['status'] = 'error' if row['errors'] else 'ok' if choices else 'informational' if row['informational_items'] or skill in ('social', 'safety') else 'unknown'
        row['evidence'] = [e for i, e in enumerate(row['evidence']) if e not in row['evidence'][:i]]
        for evidence in row['evidence']:
            if evidence not in sources:
                sources.append(evidence)
        results.append(row)
    prefix = 'DEMO — fictional places and scenarios. ' if mode == 'demo' else ''
    names={s['id']:s['name'] for s in SKILLS}
    reply = prefix + '\n'.join(f"{names[r['skill']]}：{len(r['candidates'])} 個有來源候選；" + '、'.join(str(p['name']) for p in r['candidates'][:3])  + (f"；{len(r['informational_items'])} 則未關聯地圖的來源資料" if r['informational_items'] else '') for r in results)
    if any(r['unknowns'] or r['errors'] for r in results):
        reply += '\n部分条件或资料待确认；不保证饮食安全、价格、无障碍或路线可行性。'
    if proposal:
        reply += '\n已生成待确认的完整行程调整方案，尚未保存；交通与时刻需重新核实。'
    return {'chat_reply': reply, 'skill_results': results, 'sources': sources, 'proposed_itinerary': proposal, 'conflicts': conflicts}
