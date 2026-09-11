import copy
from datetime import datetime
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))

class NearbyTests(unittest.TestCase):
    def req(self):
        return {"city": "澳门", "location": "大三巴", "date": "2026-09-10", "start_time": "10:00", "hours": 3, "mode": "walking", "members": [{"name": str(i), "preferences": "拍照"} for i in range(12)]}

    def test_variable_group_and_validation(self):
        from nearby_planner import validate_request
        self.assertEqual(len(validate_request(self.req())["members"]), 12)
        for field,value in (("city", "杭州"), ("hours", -1), ("members", "bad"), ("mode", "fly"), ("date", "invalid")):
            request=self.req(); request[field]=value
            with self.assertRaises(ValueError): validate_request(request)

    def test_find_origin_scopes_by_city_via_amap(self):
        # 城市范围校验现在完全交给高德的 city + citylimit 参数（不再有 Python 侧预存的经纬度边界），
        # 这里验证 find_origin() 确实把这两个参数传给了 _amap()
        import nearby_sources as sources
        raw={"_fetched_at":"test","pois":[{"id":"poi1","name":"大三巴","location":"113.54,22.19"}]}
        with patch.object(sources,"_amap",return_value=raw) as api:
            origin=sources.find_origin("澳门","大三巴")
        self.assertEqual(origin["name"],"大三巴")
        called_endpoint,called_params=api.call_args[0]
        self.assertEqual(called_endpoint,"place/text")
        self.assertEqual(called_params["city"],"澳门")
        self.assertEqual(called_params["citylimit"],"true")

    def test_plan_preserves_all_members_and_unknowns(self):
        import nearby_planner as planner
        origin={"id":"start","name":"起点","lng":113.54,"lat":22.19,"crs":"WGS84","source_url":"https://www.openstreetmap.org/node/1"}
        poi={"id":"node/2","name":"博物馆","lng":113.541,"lat":22.191,"category":"museum","crs":"WGS84","source_url":"https://www.openstreetmap.org/node/2","opening_hours":None,"price":None}
        route={"available":True,"duration_min":10,"distance_m":500,"coordinates":[[113.54,22.19],[113.541,22.191]],"steps":["步行前往"],"source_url":"https://routing.openstreetmap.de/","crs":"WGS84"}
        with patch.object(planner.sources,"find_origin",return_value=origin), patch.object(planner.sources,"nearby",return_value=[poi]), patch.object(planner.sources,"route",return_value=route), patch.object(planner.sources,"weather",return_value={"available":False,"reminders":["weather unavailable"]}), patch.object(planner,"rank_candidates",return_value=[poi]):
            result=planner.build_plan(self.req())
        self.assertEqual(len(result["request"]["members"]),12)
        self.assertEqual(result["stops"][0]["arrival_time"],"10:10")
        self.assertEqual(result["stops"][0]["end_time"],"10:55")
        self.assertIsNone(result["stops"][0]["price"])
        self.assertIn("营业时间", " ".join(result["reminders"]))

    def test_missing_route_never_draws_fake_line(self):
        import nearby_planner as planner
        origin={"id":"start","name":"起点","lng":113.54,"lat":22.19,"crs":"WGS84"}
        poi={**origin,"id":"node/2","name":"景点","lat":22.191,"category":"attraction"}
        with patch.object(planner.sources,"find_origin",return_value=origin), patch.object(planner.sources,"nearby",return_value=[poi]), patch.object(planner.sources,"route",return_value={"available":False,"coordinates":[],"steps":[]}), patch.object(planner.sources,"weather",return_value={"available":False,"reminders":[]}), patch.object(planner,"rank_candidates",return_value=[poi]):
            result=planner.build_plan(self.req())
        self.assertFalse(result["schedule_verified"])
        self.assertEqual(result["legs"][0]["coordinates"],[])
        self.assertEqual(result["stops"][0]["arrival_time"],"待确认交通时间")

    def test_weather_matches_trip_hours(self):
        from nearby_sources import summarize_weather
        hours=[{"time":"2026-09-10T10:00","temperature_c":31,"rain_probability":0.2,"condition_text":"多云"},
               {"time":"2026-09-10T11:00","temperature_c":33,"rain_probability":0.8,"condition_text":"雷阵雨"},
               {"time":"2026-09-11T10:00","temperature_c":20,"rain_probability":0,"condition_text":"晴"}]
        summary=summarize_weather(hours,datetime(2026,9,10,10),datetime(2026,9,10,12))
        self.assertEqual(summary["max_rain_probability"],80)
        self.assertTrue(summary["reminders"])
        self.assertEqual(summary["temperature_max"],33)

if __name__ == "__main__": unittest.main()
