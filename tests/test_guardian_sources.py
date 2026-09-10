import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'orchestrator'))
STAMP = '2026-09-10T02:00:00+00:00'

class GuardianSourcesTests(unittest.TestCase):
    def setUp(self):
        self.s = importlib.import_module('guardian_sources')
        self.mock = patch.object(self.s.nearby_sources, '_amap')
        self.api = self.mock.start()
        self.addCleanup(self.mock.stop)

    def response(self, **data):
        return dict(status='1', _fetched_at=STAMP, **data)

    def test_routes(self):
        for mode in ('walking', 'driving'):
            self.api.return_value = self.response(route={'paths': [{'distance': '100', 'duration': '90', 'steps': [{'instruction': 'walk', 'polyline': '113.5,22.1;113.6,22.2'}]}]})
            result = self.s.route('113.5,22.1', '113.6,22.2', '澳门', mode)
            self.assertTrue(result['available'])
            self.assertEqual(result['duration_s'], 90)
            self.assertEqual(result['coordinates'], [[113.5,22.1],[113.6,22.2]])
            self.assertEqual(self.api.call_args.args[0], 'direction/' + mode)

    def test_actual_transit(self):
        segments = [{'walking': {'steps': [{'instruction': 'walk to stop', 'polyline': '113.5,22.1;113.51,22.11'}]}, 'bus': {'buslines': [{'name': '3', 'departure_stop': {'name': 'A'}, 'arrival_stop': {'name': 'B'}, 'polyline': '113.51,22.11;113.6,22.2'}]}}]
        self.api.return_value = self.response(route={'transits': [{'duration': '600', 'cost': '6', 'segments': segments}]})
        result = self.s.route({'lng':113.5,'lat':22.1}, [113.6,22.2], '澳门', 'transit')
        self.assertTrue(result['available'])
        self.assertEqual(result['segments'], segments)
        self.assertEqual(result['price'], 6)
        self.assertEqual(self.api.call_args.args[0], 'direction/transit/integrated')
        self.assertEqual(self.api.call_args.args[1]['city'], '澳门')
        self.assertEqual(result['fetched_at'], STAMP)

    def test_unavailable_and_unsupported(self):
        for payload in ({'route': {'transits': []}}, {'status':'0'}, {'route':None}):
            self.api.return_value = self.response(**payload) if 'status' not in payload else payload
            result = self.s.route('113,22','114,22','香港','transit')
            self.assertFalse(result['available'])
            self.assertEqual(result['coordinates'], [])
            self.assertEqual(result['steps'], [])
            self.assertTrue(result['official_links'])
        self.api.reset_mock()
        self.assertFalse(self.s.route('113,22','114,22','香港','flight')['available'])
        self.assertFalse(self.s.route('nan,22','114,22','香港')['available'])
        self.api.assert_not_called()

    def test_failure_does_not_leak_exception(self):
        self.api.side_effect = RuntimeError('secret-in-request-url')
        for result in (self.s.route('113,22','114,22','澳门'), self.s.weather('澳门'), self.s.fetch_context('澳门')):
            self.assertNotIn('secret-in-request-url', json.dumps(result))
            self.assertTrue(result['errors'])

    def test_weather_preserves_raw_granularity_and_partial_failure(self):
        live = {'city':'澳门','reporttime':'2026-09-10 10:00:00','temperature':'30'}
        forecast = {'city':'澳门','reporttime':'2026-09-10 08:00:00','casts':[{'date':'2026-09-10','daytemp':'31','nighttemp':'26'}]}
        self.api.side_effect = [self.response(lives=[live]), self.response(forecasts=[forecast])]
        result = self.s.weather('澳门')
        self.assertEqual(result['live']['records'], [live])
        self.assertEqual(result['forecast']['records'], [forecast])
        self.assertEqual(result['forecast']['granularity'], 'daily')
        self.assertEqual(result['live']['fetched_at'], STAMP)
        self.assertEqual([c.args[1]['extensions'] for c in self.api.call_args_list], ['base','all'])
        self.api.side_effect = [RuntimeError('fail'), self.response(forecasts=[forecast])]
        self.assertTrue(self.s.weather('澳门')['available'])

    def test_context_schema_unknowns_and_when(self):
        self.api.return_value = self.response(pois=[{'id':'B123','name':'Shop','location':'113.54,22.1','type':'food'}])
        with patch.object(self.s, 'weather', return_value={'available':False,'errors':[]}):
            result = self.s.fetch_context('澳门', when='2030-01-01')
        self.assertEqual(set(result), {'pois','weather','evidence','errors'})
        poi = result['pois'][0]
        self.assertEqual(poi['amap_id'], 'B123')
        self.assertEqual(poi['data_kind'], 'real')
        self.assertIsNone(poi['price'])
        self.assertIsNone(poi['currency'])
        self.assertIn('price', poi['unknowns'])
        self.assertEqual(result['evidence'][0]['id'], poi['id'])
        self.assertFalse(result['weather']['requested_date_available'])

    def test_content_validation_and_matching(self):
        record = dict(id='c1',amap_id='B123',name='Shop',data_kind='curated',source='Editor',source_url='https://example.org/article',fetched_at=STAMP)
        records = self.s.validate_content([record])
        self.assertIsNone(records[0]['price'])
        self.assertEqual(len(self.s.match_content({'amap_id':'B123'}, records)), 1)
        self.assertEqual(self.s.match_content({'amap_id':'other','name':'Shop'}, records), [])
        demo = self.s.validate_content([dict(id='d1',name='Shop',data_kind='demo',amap_id='B123')])
        self.assertEqual(self.s.match_content({'amap_id':'B123'}, demo), [])
        for change in ({'source_url':'javascript:alert(1)'},{'source':None},{'price':-1},{'price':float('nan')},{'data_kind':'REAL'},{'fetched_at':'bad'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.s.validate_content([{**record, **change}])
        with self.assertRaises(ValueError): self.s.validate_content([record,record])
        with self.assertRaises(ValueError): self.s.validate_content({'records':[]})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'content.json'
            path.write_text(json.dumps([record]), encoding='utf-8')
            self.assertEqual(self.s.load_content(path), records)
        self.assertTrue(all(v['status']=='unconfigured' and not v['available'] for v in self.s.provider_status().values()))


class GuardianHTTPTests(unittest.TestCase):
    def test_shared_http_success_failure_and_api_rejection(self):
        import types
        import guardian_sources as s
        import requests
        from unittest.mock import Mock
        response = Mock()
        response.json.return_value = {'status':'1','route':{'paths':[{'distance':'3','duration':'2','steps':[{'instruction':'walk'}]}]}}
        # Replace credential provider entirely; never read actual configuration.
        with patch.dict(sys.modules, {'map_tool':types.SimpleNamespace(_get_key=lambda:'mock-test-only')}), patch.object(s.nearby_sources, '_CACHE', {}), patch.object(s.nearby_sources.time, 'sleep'), patch.object(s.nearby_sources.requests, 'get', return_value=response) as get:
            result = s.route('113,22','114,22','香港')
            self.assertTrue(result['available'])
            self.assertEqual(get.call_args.args[0], 'https://restapi.amap.com/v3/direction/walking')
            self.assertEqual(get.call_args.kwargs['timeout'], 25)
            get.side_effect = requests.Timeout('must-not-escape')
            self.assertFalse(s.route('113,22','114,22','香港','driving')['available'])
            get.side_effect = None
            response.json.return_value = {'status':'0','info':'denied'}
            self.assertFalse(s.route('113,22','114,22','香港','transit')['available'])

    def test_geometry_gaps_and_malformed_weather(self):
        import guardian_sources as s
        payload = {'status':'1','route':{'paths':[{'steps':[{'polyline':'113,22;114,22'},{'polyline':'115,22;116,22'}]}]}}
        with patch.object(s.nearby_sources, '_amap', return_value=payload):
            result = s.route('113,22','116,22','香港')
            self.assertEqual(result['coordinates'], [])
            self.assertEqual(len(result['coordinate_parts']), 2)
        for forecasts in ([{}], [{'reporttime':'2026-09-10 10:00:00','casts':[None]}]):
            with patch.object(s.nearby_sources, '_amap', return_value={'status':'1','lives':[{}],'forecasts':forecasts}):
                self.assertFalse(s.weather('香港')['available'])

    def test_content_file_failures_and_ambiguous_city(self):
        import guardian_sources as s
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'content.json'
            for value in ('invalid', '[NaN]', '[{"id":"x"}]'):
                path.write_text(value, encoding='utf-8')
                with self.assertRaises(ValueError): s.load_content(path)
        with patch.object(s.nearby_sources, '_amap', return_value={'status':'1','districts':[]}):
            self.assertFalse(s.weather('ambiguous')['available'])

    def test_reject_empty_route_objects_and_bad_when(self):
        import guardian_sources as s
        for mode, field, path in [('transit','transits',{'segments':[{}]}),('walking','paths',{'steps':[]})]:
            with patch.object(s.nearby_sources, '_amap', return_value={'status':'1','route':{field:[path]}}):
                self.assertFalse(s.route('113,22','114,22','香港',mode)['available'])
        with patch.object(s.nearby_sources, '_amap', return_value={'status':'1','pois':[]}), patch.object(s,'weather',return_value={'available':False,'errors':[]}):
            self.assertTrue(s.fetch_context('香港', when='2030-01-01 garbage')['errors'])

class GuardianFactsTests(unittest.TestCase):
    def test_facts_preserve_nested_skill_inputs_without_merging(self):
        import guardian_sources as s
        facts = {'diet': {'vegetarian': True, 'allergens': ['peanut'], 'halal': None},
                 'accessibility': {'wheelchair': 'entrance only', 'steps': 2},
                 'photo_spots': [{'name': 'terrace', 'time': 'sunset'}]}
        record = {'id':'facts-1', 'amap_id':'B123', 'name':'Note', 'data_kind':'curated',
                  'source':'Editor', 'source_url':'https://example.org/evidence',
                  'fetched_at':STAMP, 'facts':facts}
        normalized = s.validate_content([record])
        self.assertEqual(normalized[0]['facts'], facts)
        poi = {'amap_id':'B123', 'price':None}
        matched = s.match_content(poi, normalized)
        self.assertEqual(matched[0]['facts'], facts)
        matched[0]['facts']['diet']['allergens'].append('milk')
        self.assertEqual(facts['diet']['allergens'], ['peanut'])
        self.assertNotIn('facts', poi)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'content.json'
            path.write_text(json.dumps([record]), encoding='utf-8')
            self.assertEqual(s.load_content(path)[0]['facts'], facts)

    def test_facts_reject_non_json_and_excessive_nesting(self):
        import guardian_sources as s
        record = {'id':'demo', 'name':'Example', 'data_kind':'demo'}
        nested = {}
        for _ in range(10): nested = {'next':nested}
        for facts in ([], {'bad':float('nan')}, {'bad':set()}, {1:'bad key'},
                      {'bad':lambda: None}, nested, {'too_long':'x'*20001}, {'many':list(range(1001))}):
            with self.subTest(facts_type=type(facts).__name__), self.assertRaises(ValueError):
                s.validate_content([{**record, 'facts':facts}])

    def test_missing_key_never_reaches_http_or_throttle(self):
        import types
        import guardian_sources as s
        from unittest.mock import Mock
        missing_key = Mock(side_effect=RuntimeError('credential unavailable'))
        with patch.dict(sys.modules, {'map_tool':types.SimpleNamespace(_get_key=missing_key)}), patch.object(s.nearby_sources.requests, 'get') as get, patch.object(s.nearby_sources.time, 'sleep') as sleep:
            context = s.fetch_context('澳门')
            self.assertEqual(context['pois'], [])
            self.assertFalse(context['weather']['available'])
            self.assertTrue(context['errors'])
            for mode in ('walking','driving','transit'):
                self.assertFalse(s.route('113,22','114,22','澳门',mode)['available'])
            get.assert_not_called()
            sleep.assert_not_called()

class GuardianSearchTests(unittest.TestCase):
    def test_unrestricted_around_and_keyword_search(self):
        import guardian_sources as s
        with patch.object(s.nearby_sources, '_amap', return_value={'status':'1','pois':[]}) as api, patch.object(s,'weather',return_value={'available':False,'errors':[]}):
            for keyword in ('toilet','shopping','DIY'):
                s.fetch_context('澳门', {'lng':113.54,'lat':22.19,'keywords':keyword})
                endpoint, params = api.call_args.args
                self.assertEqual(endpoint, 'place/around')
                self.assertEqual(params['keywords'], keyword)
                self.assertNotIn('types', params)
            s.fetch_context('香港', {'keywords':'DIY'})
            endpoint, params = api.call_args.args
            self.assertEqual(endpoint, 'place/text')
            self.assertEqual(params['keywords'], 'DIY')
            self.assertNotIn('types', params)
            s.fetch_context('香港', {'location':'114.17,22.3','keywords':'toilet'})
            self.assertEqual(api.call_args.args[0], 'place/around')

    def test_city_bbox_for_aliases_and_coordinate_queries(self):
        import guardian_sources as s
        pois = [{'id':'macau','name':'A','location':'113.54,22.19'},
                {'id':'hk','name':'B','location':'114.17,22.3'},
                {'id':'outside','name':'C','location':'120,30'}]
        with patch.object(s.nearby_sources,'_amap',return_value={'status':'1','pois':pois}), patch.object(s,'weather',return_value={'available':False,'errors':[]}):
            for city in ('澳门','澳門','Macau','Macao','820000'):
                self.assertEqual([p['id'] for p in s.fetch_context(city)['pois']], ['macau'])
            for city in ('香港','Hong Kong','810000'):
                self.assertEqual([p['id'] for p in s.fetch_context(city, '114.17,22.3')['pois']], ['hk'])

if __name__ == '__main__': unittest.main()
