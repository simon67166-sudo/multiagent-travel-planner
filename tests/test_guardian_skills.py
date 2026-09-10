import copy
import importlib.util
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'orchestrator'))

IDS = 'food_risk group_consensus queue weather toilet souvenir citywalk crowd safety social hidden_menu diy lazy museum photo'.split()

class GuardianSkillsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('guardian_skills'), 'skills engine must exist')
        from guardian_skills import run_skills, validate_preferences
        from guardian_demo import load_demo
        self.run_skills = run_skills
        self.validate = validate_preferences
        self.demo = load_demo

    def test_all_skills_execute_and_report_unknowns(self):
        for skill in IDS:
            with self.subTest(skill=skill):
                result = self.run_skills(skill, self.demo('Hong Kong'), [skill])
                row = result['skill_results'][0]
                self.assertEqual(row['skill'], skill)
                self.assertTrue(row['actions'])
                self.assertTrue(row['evidence'])
                self.assertFalse(row['errors'])
                empty = self.run_skills(skill, {'city': '香港', 'mode': 'real'}, [skill])
                self.assertTrue(empty['skill_results'][0]['unknowns'])
                self.assertIsNone(empty['proposed_itinerary'])

    def test_group_sizes_and_fairness(self):
        for size in [1, 4, 12, 250]:
            context = self.demo('Macau')
            context['members'] = [{'id': str(i), 'preferences': {'soft': {'culture': i % 2}}} for i in range(size)]
            result = self.run_skills('', context, ['group_consensus'])
            candidate = result['skill_results'][0]['candidates'][0]
            self.assertEqual(len(candidate['member_scores']), size)
            self.assertAlmostEqual(candidate['score'], (candidate['average_score'] + candidate['minimum_score']) / 2)

    def test_canonical_preferences(self):
        self.assertEqual(self.validate({}), {'diet': [], 'budget': None, 'walking_limit_m': None, 'accessibility': False, 'soft': dict.fromkeys(['photo', 'culture', 'shopping', 'rest'], 0.5)})
        self.assertEqual(self.validate({'diet': '素食, no peanuts', 'budget': {'amount': 0, 'currency': 'hkd'}, 'accessibility': 'false'})['budget'], {'amount': 0.0, 'currency': 'HKD'})
        for value in [{'budget': {'amount': -1, 'currency': 'HKD'}}, {'walking_limit_m': float('nan')}, {'soft': {'photo': 2}}, {'accessibility': 'maybe'}, {'diet': [1]}, {'budget': {'amount': 2}}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(value)

    def test_hard_filters_currency_and_unknown(self):
        context = self.demo('HK')
        context['members'] = [{'preferences': {'diet': ['vegan'], 'budget': {'amount': 100, 'currency': 'HKD'}, 'walking_limit_m': 500, 'accessibility': True}}]
        base = copy.deepcopy(context['pois'][0])
        base.update(id='good', diet=['vegan'], price={'amount': 50, 'currency': 'HKD'}, walking_distance_m=100, accessibility=True)
        context['pois'] = [base]
        for field, value in [('price', {'amount': 50, 'currency': 'MOP'}), ('walking_distance_m', 700), ('accessibility', False), ('diet', [])]:
            bad = dict(base, id=field)
            bad[field] = value
            context['pois'].append(bad)
        unknown = dict(base, id='unknown')
        unknown.pop('price')
        context['pois'].append(unknown)
        result = self.run_skills('', context, ['food_risk'])
        rows = result['skill_results'][0]['candidates']
        self.assertEqual([r['id'] for r in rows], ['good'])
        self.assertTrue(result['skill_results'][0]['unknowns'])
        self.assertTrue(result['conflicts'])

    def test_schedule_proposals_are_canonical_and_do_not_mutate(self):
        for skill in ['queue', 'weather', 'lazy']:
            context = self.demo('Macau')
            before = copy.deepcopy(context)
            result = self.run_skills('', context, [skill])
            proposal = result['proposed_itinerary']
            self.assertIsNotNone(proposal, skill)
            self.assertEqual(context, before)
            self.assertEqual(set(proposal), set(context['itinerary']))
            self.assertNotEqual(proposal['trip_plan']['days'], before['itinerary']['trip_plan']['days'])
            self.assertEqual(proposal['trip_plan']['hotels'], before['itinerary']['trip_plan']['hotels'])
            for day in proposal['trip_plan']['days'].values():
                seen = set()
                node = day['head_id']
                while node is not None:
                    self.assertNotIn(node, seen)
                    seen.add(node)
                    node = day['nodes'][node]['next_id']
                self.assertEqual(seen, set(day['nodes']))

    def test_selection_and_demo_separation(self):
        result = self.run_skills('下雨 排隊 拍照', self.demo('HK'))
        self.assertTrue({'weather', 'queue', 'photo'} <= {r['skill'] for r in result['skill_results']})
        context = self.demo('HK')
        context['mode'] = 'real'
        result = self.run_skills('', context, IDS + ['invented'])
        self.assertFalse(result['sources'])
        self.assertTrue(all(not r['candidates'] for r in result['skill_results']))
        self.assertTrue(result['conflicts'])

    def test_demo_is_deterministic_and_has_no_fake_map_lines(self):
        for city, currency in [('HK', 'HKD'), ('Macau', 'MOP')]:
            context = self.demo(city)
            self.assertEqual(context, self.demo(city))
            self.assertTrue(all(p['demo'] and 'DEMO' in p['name'] for p in context['pois']))
            self.assertTrue(all(p['price']['currency'] == currency for p in context['pois']))
            self.assertNotIn('coordinates', repr(context))
            self.assertTrue(all(p['fetched_at'] for p in context['pois']))

if __name__ == '__main__':
    unittest.main()

class GuardianSkillsIntegrationTests(unittest.TestCase):
    def test_public_registry(self):
        import guardian_skills as engine
        self.assertEqual({r['id'] for r in engine.SKILLS}, set(IDS))

    def test_full_itinerary_cumulative_and_unknown(self):
        from guardian_skills import validate_itinerary_feasibility as check
        members = [{'preferences': {'budget': {'amount': 100, 'currency': 'HKD'}, 'walking_limit_m': 500}}]
        plan = {'nearby_plan': {'stops': [{'id': 'a', 'price': 60, 'currency': 'HKD', 'internal_walking_m': 0}, {'id': 'b', 'price': 60, 'currency': 'HKD', 'internal_walking_m': 0}],
                'legs': [{'available': True, 'mode': 'walking', 'distance_m': 300, 'price': 0, 'currency': 'HKD'}, {'available': True, 'mode': 'walking', 'distance_m': 300, 'price': 0, 'currency': 'HKD'}], 'cost_complete': True, 'walking_complete': True}}
        result = check(plan, members)
        self.assertEqual(result['status'], 'infeasible')
        self.assertEqual(result['total_cost_by_currency'], {'HKD': 120})
        self.assertEqual(result['known_walking_m'], 600)
        plan['nearby_plan']['stops'][1]['price'] = 10
        plan['nearby_plan']['legs'][1]['distance_m'] = 100
        self.assertTrue(check(plan, members)['feasible'])
        plan['nearby_plan']['legs'].pop()
        self.assertEqual(check(plan, members)['status'], 'unknown')
        plan['nearby_plan']['stops'][1]['currency'] = 'MOP'
        self.assertEqual(check(plan, members)['status'], 'infeasible')

    def test_transit_and_shared_price_not_guaranteed(self):
        from guardian_skills import validate_itinerary_feasibility as check
        members = [{'preferences': {'budget': {'amount': 100, 'currency': 'HKD'}, 'walking_limit_m': 500}}]
        plan = {'nearby_plan': {'stops': [{'price': 40, 'currency': 'HKD', 'internal_walking_m': 0, 'cost_scope': 'group'}], 'legs': [{'mode': 'transit', 'available': True, 'distance_m': 10000, 'walking_distance_m': 600, 'price': 5, 'currency': 'HKD'}], 'cost_complete': True, 'walking_complete': True}}
        result = check(plan, members)
        self.assertIn('total walking maximum exceeded', result['violations'])
        self.assertIn('cost allocation per person unknown', result['unknowns'])

    def test_candidate_api_is_nonmutating_and_fail_closed(self):
        from guardian_skills import evaluate_candidates
        pois = [{'id': 'a', 'price': 10, 'currency': 'HKD'}, {'id': 'b', 'price': 10, 'currency': 'MOP'}, {'id': 'c'}]
        before = copy.deepcopy(pois)
        result = evaluate_candidates(pois, [{'preferences': {'budget': {'amount': 15, 'currency': 'HKD'}}}])
        self.assertEqual([p['id'] for p in result['eligible']], ['a'])
        self.assertEqual([p['id'] for p in result['excluded']], ['b'])
        self.assertEqual([p['id'] for p in result['unknown']], ['c'])
        self.assertEqual(pois, before)
        with self.assertRaises(ValueError):
            evaluate_candidates(pois, [{'preferences': {'budget': 5}}])

    def test_canonical_nearby_stays_modified_after_store(self):
        from guardian_skills import run_skills
        from guardian_demo import load_demo
        from guardian_store import canonical_itinerary
        import trip_plan
        for skill in ('queue', 'weather', 'lazy'):
            context = load_demo('HK')
            result = run_skills('', context, [skill])['proposed_itinerary']
            self.assertNotEqual(result['nearby_plan']['stops'], context['itinerary']['nearby_plan']['stops'])
            stored = canonical_itinerary(result)
            stops = trip_plan.day_stops(next(iter(stored['trip_plan']['days'].values())))
            self.assertEqual([s['place'] for s in stops], [s['name'] for s in result['nearby_plan']['stops']])
            self.assertFalse(result['nearby_plan']['legs'])

    def test_curated_adapter_facts_keep_separate_provenance(self):
        from guardian_skills import run_skills
        source = {'source': 'Amap', 'fetched_at': '2026-09-10T00:00:00Z', 'data_kind': 'real'}
        poi = {**source, 'id': 'a', 'amap_id': 'a', 'name': 'Actual sourced venue', 'category': 'restaurant'}
        content = {**source, 'source': 'Curated venue documentation', 'id': 'doc-a', 'amap_id': 'a', 'name': 'Venue documentation', 'data_kind': 'curated', 'facts': {'kind': 'hidden_menu', 'text': 'Documented tea option', 'diet': ['vegan']}}
        result = run_skills('', {'mode': 'real', 'pois': [poi], 'content': [content], 'members': [{'preferences': {'diet': ['vegan']}}]}, ['hidden_menu'])
        self.assertEqual(len(result['skill_results'][0]['candidates']), 1)
        self.assertEqual({s['source'] for s in result['sources']}, {'Amap', 'Curated venue documentation'})

    def test_source_timestamps_and_error_context(self):
        from guardian_skills import run_skills
        result = run_skills('', {'pois': [{'id': 'a', 'name': 'No timezone', 'source': 'test', 'fetched_at': '2026-09-10'}], 'errors': [{'source': 'adapter', 'code': 'unavailable'}]}, ['group_consensus'])
        self.assertFalse(result['skill_results'][0]['candidates'])
        self.assertTrue(result['skill_results'][0]['errors'])

class GuardianSkillsEdgeTests(unittest.TestCase):
    def test_lazy_handles_existing_legacy_without_optional_flag(self):
        from guardian_demo import load_demo
        from guardian_skills import run_skills
        context = load_demo('HK')
        context['itinerary']['nearby_plan'] = None
        for node in context['itinerary']['trip_plan']['days']['2026-09-10']['nodes'].values():
            node.pop('optional')
            node['poi'].pop('optional')
        proposal = run_skills('', context, ['lazy'])['proposed_itinerary']
        self.assertIsNotNone(proposal)
        self.assertEqual(len(proposal['trip_plan']['days']['2026-09-10']['nodes']), 1)

    def test_huge_numeric_preference_rejected_as_value_error(self):
        from guardian_skills import validate_preferences
        with self.assertRaises(ValueError):
            validate_preferences({'walking_limit_m': 10 ** 400})

    def test_queue_weather_crowd_toilet_have_different_decisions(self):
        from guardian_demo import load_demo
        from guardian_skills import run_skills
        context = load_demo('HK')
        result = run_skills('', context, ['queue', 'crowd', 'toilet', 'weather'])
        rows = {r['skill']: r for r in result['skill_results']}
        self.assertEqual([p['queue_minutes'] for p in rows['queue']['candidates']], [5, 10, 45])
        self.assertEqual([p['crowd_level'] for p in rows['crowd']['candidates']], [0.2, 0.4, 0.9])
        self.assertEqual([p['walking_distance_m'] for p in rows['toilet']['candidates']], [200, 300])
        self.assertTrue(rows['weather']['actions'][0]['adverse'])
        context['weather']['adverse'] = False
        context['weather']['max_rain_probability'] = 0
        self.assertIsNone(run_skills('', context, ['weather'])['proposed_itinerary'])

    def test_malformed_legacy_chain_does_not_hang_or_mutate(self):
        from guardian_demo import load_demo
        from guardian_skills import run_skills
        context = load_demo('HK')
        context['itinerary']['trip_plan']['days']['2026-09-10']['nodes']['n3']['next_id'] = 'n1'
        before = copy.deepcopy(context)
        result = run_skills('', context, ['queue'])
        self.assertTrue(result['skill_results'][0]['errors'])
        self.assertIsNone(result['proposed_itinerary'])
        self.assertEqual(context, before)

    def test_bookings_and_meals_are_not_removed_or_reordered(self):
        from guardian_demo import load_demo
        from guardian_skills import run_skills
        context = load_demo('HK')
        context['itinerary']['nearby_plan'] = None
        day = context['itinerary']['trip_plan']['days']['2026-09-10']
        day['nodes']['n2']['booking_ref'] = 'keep-me'
        day['nodes']['n3']['type'] = 'meal'
        self.assertIsNone(run_skills('', context, ['lazy'])['proposed_itinerary'])
        self.assertIsNone(run_skills('', context, ['queue'])['proposed_itinerary'])

    def test_curated_conflicting_facts_fail_closed_and_no_name_join(self):
        from guardian_skills import run_skills
        source = {'source': 'test', 'fetched_at': '2026-09-10T00:00:00Z'}
        poi = {**source, 'id': 'a', 'amap_id': 'a', 'name': 'Venue', 'diet': ['vegan']}
        content = {**source, 'id': 'doc', 'amap_id': 'a', 'facts': {'diet': ['halal']}}
        result = run_skills('', {'pois': [poi], 'content': [content]}, ['group_consensus'])
        self.assertFalse(result['skill_results'][0]['candidates'])
        self.assertTrue(result['conflicts'])
        content['amap_id'] = 'different'
        content['name'] = 'Venue'
        result = run_skills('', {'pois': [poi], 'content': [content]}, ['group_consensus'])
        self.assertEqual(len(result['skill_results'][0]['candidates']), 1)

    def test_fairness_does_not_ignore_minority(self):
        from guardian_skills import evaluate_candidates
        members = [{'id': 'photo', 'preferences': {'soft': {'photo': 1, 'culture': 0, 'shopping': 0, 'rest': 0}}},
                   {'id': 'culture', 'preferences': {'soft': {'photo': 0, 'culture': 1, 'shopping': 0, 'rest': 0}}}]
        result = evaluate_candidates([{'id': 'polarized', 'soft': {'photo': 1, 'culture': 0}}, {'id': 'balanced', 'soft': {'photo': 0.6, 'culture': 0.6}}], members)
        self.assertEqual(result['eligible'][0]['id'], 'balanced')
        self.assertEqual(result['eligible'][1]['minimum_score'], 0)

    def test_every_skill_is_json_serializable_and_explicit_empty_selection(self):
        import json
        from guardian_demo import load_demo
        from guardian_skills import run_skills
        output = run_skills('', load_demo('Macau'), IDS)
        json.dumps(output, allow_nan=False)
        self.assertEqual(run_skills('weather', {}, [])['skill_results'], [])

class GuardianSkillsReviewTests(unittest.TestCase):
    def test_social_is_ready_to_show_script_without_pois(self):
        from guardian_skills import run_skills
        row = run_skills('社恐，帮我点餐和问路', {'city': '香港'}, ['social'])['skill_results'][0]
        self.assertTrue(row['actions'])
        self.assertTrue(row['actions'][0]['ready_to_show'])
        self.assertGreaterEqual(len(row['actions'][0]['dialogue']), 2)
        self.assertTrue(row['unknowns'])
        self.assertNotIn('event', row['actions'][0])

    def test_five_specialist_roles_and_chinese_reply(self):
        from guardian_skills import SKILLS, run_skills
        self.assertEqual({s['agent'] for s in SKILLS}, {'Group', 'FoodShopping', 'Mobility', 'Guardian', 'Experience'})
        self.assertIn('待确认', run_skills('拍照', {}, ['photo'])['chat_reply'])

    def test_nonfood_venues_not_blocked_by_diet(self):
        from guardian_skills import evaluate_candidates
        result = evaluate_candidates([{'id': 'museum', 'category': 'museum'}, {'id': 'meal', 'category': 'restaurant'}, {'id': 'unclear'}], [{'preferences': {'diet': ['vegan']}}])
        self.assertEqual([p['id'] for p in result['eligible']], ['museum'])
        self.assertEqual({p['id'] for p in result['unknown']}, {'meal', 'unclear'})

    def test_skill_specific_decision_payloads(self):
        from guardian_demo import load_demo
        from guardian_skills import run_skills
        rows = {r['skill']: r for r in run_skills('', load_demo('HK'), IDS)['skill_results']}
        for skill, keys in {'queue': ['alternatives'], 'toilet': ['fee', 'floor', 'tissue'], 'souvenir': ['recipient', 'product_source', 'tax'], 'museum': ['ordered_exhibits', 'story', 'fiction'], 'photo': ['angle', 'time'], 'diy': ['price', 'materials', 'availability'], 'citywalk': ['ordered_stops', 'stories']}.items():
            with self.subTest(skill=skill):
                for key in keys:
                    self.assertIn(key, rows[skill]['actions'][0])

    def test_expired_provenance_and_stale_live_queue(self):
        from datetime import datetime, timedelta, timezone
        from guardian_skills import run_skills
        now = datetime.now(timezone.utc)
        source = {'id': 'a', 'name': 'Sourced venue', 'source': 'provider', 'fetched_at': now.isoformat(), 'queue_minutes': 5}
        expired = dict(source, valid_until=(now-timedelta(seconds=1)).isoformat())
        self.assertFalse(run_skills('', {'pois': [expired]}, ['queue'])['skill_results'][0]['candidates'])
        stale = dict(source, fetched_at=(now-timedelta(hours=4)).isoformat())
        row = run_skills('', {'pois': [stale]}, ['queue'])['skill_results'][0]
        self.assertFalse(row['candidates'])
        self.assertTrue(row['unknowns'])
        self.assertTrue(run_skills('', {'pois': [source]}, ['queue'])['skill_results'][0]['candidates'])

    def test_food_excluded_keeps_source_and_reason(self):
        from guardian_demo import load_demo
        from guardian_skills import run_skills
        context = load_demo('HK')
        context['members'] = [{'preferences': {'diet': ['vegan']}}]
        context['pois'][0]['diet'] = ['halal']
        row = run_skills('', context, ['food_risk'])['skill_results'][0]
        self.assertTrue(row['excluded'])
        self.assertTrue(row['excluded'][0]['reasons'])
        self.assertTrue(row['evidence'])
