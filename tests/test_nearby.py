from datetime import datetime
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))

class NearbyTests(unittest.TestCase):
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

    def test_weather_matches_trip_hours(self):
        from nearby_sources import summarize_weather
        hours=[{"time":"2026-09-10T10:00","temperature_c":31,"rain_probability":0.2,"condition_text":"多云"},
               {"time":"2026-09-10T11:00","temperature_c":33,"rain_probability":0.8,"condition_text":"雷阵雨"},
               {"time":"2026-09-11T10:00","temperature_c":20,"rain_probability":0,"condition_text":"晴"}]
        summary=summarize_weather(hours,datetime(2026,9,10,10),datetime(2026,9,10,12))
        self.assertEqual(summary["max_rain_probability"],80)
        self.assertTrue(summary["reminders"])
        self.assertEqual(summary["temperature_max"],33)

    def test_place_day_preserves_real_arrival_times(self):
        # 2026-09-14：nearby 并入达人 Agent 后，排班逻辑搬进了 route_agent.schedule()/_place_day()，
        # 这条是原 test_plan_preserves_all_members_and_unknowns 的等价替代
        import trip_plan
        from agents import route_agent
        origin={"lng":113.54,"lat":22.19,"name":"起点"}
        poi={"lng":113.541,"lat":22.191,"place":"博物馆","category":"museum"}
        leg={"mode":"walking","duration_min":10,"distance_m":500}
        trip = trip_plan.new_trip_plan("test-place-day")
        day_plan = trip_plan.get_or_create_day(trip, "day-1")
        with patch.object(route_agent, "_real_leg", return_value=leg):
            unscheduled, _ = route_agent._place_day(day_plan, [poi], origin, "10:00", 5, "澳门")
        self.assertEqual(unscheduled, [])
        stops = trip_plan.day_stops(day_plan)
        self.assertEqual(stops[0]["arrival_time"], "10:10")
        self.assertEqual(stops[0]["end_time"], "10:40")  # museum 不含"食"字，走 30 分钟停留档

    def test_place_day_stores_coordinates_on_stop(self):
        # 2026-09-15：修复"地图重新按名字地理编码导致坐标不可靠"的 bug——候选自带的真实坐标
        # 要顺手存进 trip_plan 节点里，不能排完班就扔掉，不然 schedule_widgets.py 只能靠
        # 不可靠的 map_tool.geocode() 重新猜一次
        import trip_plan
        from agents import route_agent
        origin={"lng":113.54,"lat":22.19,"name":"起点"}
        poi={"lng":113.541,"lat":22.191,"place":"博物馆","category":"museum"}
        leg={"mode":"walking","duration_min":10,"distance_m":500}
        trip = trip_plan.new_trip_plan("test-place-day-coords")
        day_plan = trip_plan.get_or_create_day(trip, "day-1")
        with patch.object(route_agent, "_real_leg", return_value=leg):
            route_agent._place_day(day_plan, [poi], origin, "10:00", 5, "澳门")
        stops = trip_plan.day_stops(day_plan)
        self.assertEqual(stops[0]["lng"], 113.541)
        self.assertEqual(stops[0]["lat"], 22.191)

    def test_real_leg_skips_geocoding_when_coordinates_known(self):
        # 候选/起点带坐标（schedule() 传进来的都带，因为 content_agent.py 已经用
        # nearby_sources.find_origin()/nearby() 查过一次）就该直接按坐标查路线，不该再用
        # map_tool.geocode() 按名字重新查一次——对港澳同名地点场景不可靠，是这个 session
        # 已经踩过并绕开的坑
        import map_tool
        from agents import route_agent
        a={"lng":113.54,"lat":22.19,"place":"甲地"}
        b={"lng":113.541,"lat":22.191,"place":"乙地"}
        fake_route={"available":True,"distance_m":300,"duration_min":5,"mode":"walking","coordinates":[]}
        with patch.object(map_tool,"geocode",side_effect=AssertionError("不该调用 geocode()")), \
             patch("nearby_sources.route",return_value=fake_route) as route_mock:
            leg = route_agent._real_leg(a, b, "澳门")
        self.assertEqual(leg["distance_m"], 300)
        route_mock.assert_called_once()

    def test_content_agent_dedupes_seeds_by_place(self):
        # 2026-09-15：修复"同一个地点的两条不同帖子都被选成种子"的 bug——真实测过会出现
        # 两条不同的人写的"叠记咖喱美食"帖子同时被当成种子推荐给用户，同一家店等于推荐了两次
        from agents import content_agent
        posts = [
            {"place": "叠记咖喱美食", "post_id": "p1", "similarity_score": 0.9, "category": "Food"},
            {"place": "文记咖啡", "post_id": "p2", "similarity_score": 0.8, "category": "Food"},
            {"place": "叠记咖喱美食", "post_id": "p3", "similarity_score": 0.7, "category": "Food"},
        ]
        deduped = content_agent._dedupe_by_place(posts)
        places = [p["place"] for p in deduped]
        self.assertEqual(places, ["叠记咖喱美食", "文记咖啡"])
        self.assertEqual(deduped[0]["post_id"], "p1")  # 保留相似度更高的那条（p1 而不是 p3）

    def test_place_day_never_fakes_a_failed_route(self):
        # 原 test_missing_route_never_draws_fake_line 的等价替代：查路线失败时不能编造到达时间，
        # 要老实标"待定（地图查询失败）"，不能假装查到了什么
        import trip_plan
        from agents import route_agent
        origin={"lng":113.54,"lat":22.19,"name":"起点"}
        poi={"lng":113.541,"lat":22.191,"place":"景点"}
        trip = trip_plan.new_trip_plan("test-place-day-fail")
        day_plan = trip_plan.get_or_create_day(trip, "day-1")
        with patch.object(route_agent, "_real_leg", return_value=None):
            unscheduled, _ = route_agent._place_day(day_plan, [poi], origin, "10:00", 5, "澳门")
        self.assertEqual(unscheduled, [])
        stops = trip_plan.day_stops(day_plan)
        self.assertEqual(stops[0]["arrival_transport"], "待定（地图查询失败）")

if __name__ == "__main__": unittest.main()
