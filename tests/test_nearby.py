from datetime import datetime
import json
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

    def test_nearby_plan_caps_results_per_call(self):
        # 2026-09-17：周边 POI 扩展没有截断时，一次 nearby() 请求就能回几十条，run() 里
        # mode="trip" 场景对每个种子都各自调一次 _nearby_plan()，汇总去重后是几百条量级——
        # 参考已删除的 fellow 分支 nearby_planner.py 的 pool=ranked[:8] 做法，这里改成按
        # recommendation_score 排序后直接截断，验证确实生效且没有把顺序打乱
        from agents import content_agent
        pois = [{"id": f"poi{i}", "name": f"地点{i}", "lng": 113.54, "lat": 22.19,
                 "category": "景点", "address": "test"} for i in range(20)]
        with patch.object(content_agent.nearby_sources, "nearby", return_value=pois), \
             patch.object(content_agent.store, "find_posts_by_place", return_value=[]):
            results = content_agent._nearby_plan({"lng": 113.54, "lat": 22.19, "name": "起点", "id": None}, "澳门", None)
        self.assertEqual(len(results), content_agent._NEARBY_PER_CALL_CAP)

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

    def test_candidate_role_covers_both_data_sources(self):
        # 2026-09-15：_stay_minutes() 原来只查"食"字，社区帖子 category="饮食" 能匹配上，
        # 高德 POI 的 category（原始 type 字符串，餐饮大类叫"餐饮服务"）不含"食"字，判不出来。
        # _candidate_role() 要同时覆盖两种来源
        from agents import route_agent
        self.assertEqual(route_agent._candidate_role({"category": "饮食"}), "meal")
        self.assertEqual(route_agent._candidate_role({"category": "餐饮服务;中餐厅;江浙菜"}), "meal")
        self.assertEqual(route_agent._candidate_role({"category": "景点"}), "attraction")
        self.assertEqual(route_agent._candidate_role({"category": "风景名胜;风景名胜;文物古迹"}), "attraction")
        self.assertEqual(route_agent._candidate_role({}), "attraction")  # 没有 category，安全默认值

    def test_pool_for_candidate_maps_three_categories(self):
        # 2026-09-16：排班池子从"景点/餐饮"二选一改成"景点/早餐/午晚餐"三选一，直接对应
        # content_agent.py 的分类质检打好的标签；候选没被分类过（防御性兜底）才退回
        # _candidate_role() 的粗粒度判断，食物类默认落 lunch_dinner，不落 breakfast
        from agents import route_agent
        self.assertEqual(route_agent._pool_for_candidate({"category": "景点"}), "attraction")
        self.assertEqual(route_agent._pool_for_candidate({"category": "早餐"}), "breakfast")
        self.assertEqual(route_agent._pool_for_candidate({"category": "午晚餐"}), "lunch_dinner")
        self.assertEqual(route_agent._pool_for_candidate({"category": "餐饮服务;中餐厅;江浙菜"}), "lunch_dinner")
        self.assertEqual(route_agent._pool_for_candidate({}), "attraction")

    def test_greedy_geo_cluster_groups_nearby_candidates_together(self):
        # 2026-09-17：用户反馈"排路线也该把离得比较近或者顺路的排一天"——聚类应该把地理上
        # 扎堆的候选分到同一天，不是城中/路环这种隔得很远的候选混在一天
        from agents import route_agent
        cluster_a = [
            {"place": "A1", "lng": 113.54, "lat": 22.19, "recommendation_score": 0.9},
            {"place": "A2", "lng": 113.541, "lat": 22.191, "recommendation_score": 0.8},
        ]
        cluster_b = [
            {"place": "B1", "lng": 113.60, "lat": 22.10, "recommendation_score": 0.85},
            {"place": "B2", "lng": 113.601, "lat": 22.101, "recommendation_score": 0.7},
        ]
        clusters, leftover = route_agent._greedy_geo_cluster(cluster_a + cluster_b, day_capacities=[2, 2])
        self.assertEqual(leftover, [])
        places_per_day = [{c["place"] for c in day} for day in clusters]
        self.assertIn({"A1", "A2"}, places_per_day)
        self.assertIn({"B1", "B2"}, places_per_day)

    def test_assign_by_proximity_matches_nearest_centroid(self):
        from agents import route_agent
        pool = [
            {"place": "早餐甲", "lng": 113.54, "lat": 22.19},
            {"place": "早餐乙", "lng": 113.60, "lat": 22.10},
        ]
        centroids = [{"lng": 113.541, "lat": 22.191}, {"lng": 113.601, "lat": 22.101}]
        assigned, leftover = route_agent._assign_by_proximity(pool, centroids, day_capacities=[1, 1])
        self.assertEqual(assigned[0][0]["place"], "早餐甲")
        self.assertEqual(assigned[1][0]["place"], "早餐乙")
        self.assertEqual(leftover, [])

    def test_greedy_geo_cluster_rescues_stranded_priority_pick(self):
        # 2026-09-17：用户指出"经过用户选择的景点如果被冲掉了需要排到别的时间，根据距离看看
        # 放在哪一天最合适"——容量不够导致用户勾选的候选没能在正常分组阶段占到坑位时，
        # 兜底逻辑要把它硬塞进离它最近、已经有候选的那一天，不能就这么消失在 leftover 里
        from agents import route_agent
        pool = [
            {"place": "普通景点1", "lng": 113.54, "lat": 22.19, "recommendation_score": 0.9},
            {"place": "普通景点2", "lng": 113.541, "lat": 22.191, "recommendation_score": 0.85},
            {"place": "用户勾选但分数很低", "lng": 113.542, "lat": 22.192, "recommendation_score": 0.1},
        ]
        # day_capacities=[2] 只有一天、容量2、总共3个候选——装不下全部3个，但用户勾选的那个
        # 因为 priority_places 会被优先当种子占坑，不会因为分数太低正常排队排不上号；容量
        # 有限，被挤掉的是普通候选里的一个，不是用户勾选的那个
        clusters, leftover = route_agent._greedy_geo_cluster(pool, day_capacities=[2], priority_places={"用户勾选但分数很低"})
        self.assertEqual(len(leftover), 1)
        self.assertNotIn("用户勾选但分数很低", {c["place"] for c in leftover})
        self.assertIn("用户勾选但分数很低", {c["place"] for c in clusters[0]})

    def test_flight_arrival_and_departure_reads_trip_plan_flights(self):
        from agents import route_agent
        trip = {"flights": [
            {"from_": "北京首都国际机场", "to": "澳门", "depart_time": "08:00", "arrive_time": "10:10"},
            {"from_": "澳门", "to": "北京首都国际机场", "depart_time": "18:00", "arrive_time": "20:30"},
        ]}
        arrival, departure = route_agent._flight_arrival_and_departure(trip, "澳门")
        self.assertEqual(arrival, "10:10")
        self.assertEqual(departure, "18:00")
        self.assertEqual(route_agent._flight_arrival_and_departure({"flights": []}, "澳门"), (None, None))

    def test_day_slot_templates_trims_arrival_and_departure_day(self):
        # 落地当天（抵达10:10 + 2小时缓冲=12:10）该砍掉早餐/上午景点这些太早的槽位，"午餐"
        # 名义上12:00开始但窗口到13:30，裁剪之后变成12:10-13:30还留得住，不整槽丢弃；
        # 离开当天（起飞18:00 - 3小时缓冲=15:00）该砍掉晚餐/晚上景点这些太晚的槽位，"下午"
        # 名义窗口14:00-17:30裁剪成14:00-15:00还剩1小时，同样不整槽丢弃；中间天不受影响
        from agents import route_agent
        templates = route_agent._day_slot_templates(3, arrival_time="10:10", departure_time="18:00")
        self.assertEqual({s["slot_id"] for s in templates[0]}, {"lunch", "afternoon", "dinner", "evening"})
        lunch_day0 = next(s for s in templates[0] if s["slot_id"] == "lunch")
        self.assertEqual(lunch_day0["start"], "12:10")
        self.assertEqual({s["slot_id"] for s in templates[1]}, {s["slot_id"] for s in route_agent._SLOT_TEMPLATE})
        self.assertEqual({s["slot_id"] for s in templates[2]}, {"breakfast", "morning", "lunch", "afternoon"})
        afternoon_day2 = next(s for s in templates[2] if s["slot_id"] == "afternoon")
        self.assertEqual(afternoon_day2["end"], "15:00")

    def test_schedule_shrinks_arrival_day_and_rescues_bumped_priority_pick(self):
        # 端到端验证：已订机票 20:00 才到澳门，落地当天（20:00+2小时缓冲=22:00）连晚上
        # 景点槽位都赶不上，等于这天景点容量变成 0——用户勾选的候选在这天完全没有槽位可用，
        # 不该消失，该被挪到第2天
        from agents import route_agent
        import trip_plan as tp
        trip = tp.new_trip_plan("flight-aware-test")
        tp.add_flight(trip, {"from_": "北京首都国际机场", "to": "澳门", "depart_time": "16:00", "arrive_time": "20:00"})
        state = {"trip_plan": trip}
        candidates = [
            {"place": "用户勾选的景点", "category": "景点", "lng": 113.54, "lat": 22.19, "recommendation_score": 0.9},
            {"place": "备用景点", "category": "景点", "lng": 113.541, "lat": 22.191, "recommendation_score": 0.8},
        ]
        fake_leg = {"mode": "walking", "duration_min": 5, "distance_m": 300}
        with patch.object(route_agent, "_real_leg", return_value=fake_leg), \
             patch.object(route_agent.llm_tool, "call_llm", return_value="[]"), \
             patch.object(route_agent.weather_tool, "get_hourly_forecast", return_value=[]):
            route_agent.schedule(
                state, candidates, city="澳门", mode="trip", time_budget_days=2,
                priority_places={"用户勾选的景点"},
            )
        day1_places = {s["place"] for s in tp.day_stops(trip["days"].get("day-1", {"head_id": None, "nodes": {}}))}
        day2_places = {s["place"] for s in tp.day_stops(trip["days"].get("day-2", {"head_id": None, "nodes": {}}))}
        self.assertNotIn("用户勾选的景点", day1_places)  # 落地当天景点容量是 0，排不进去
        self.assertIn("用户勾选的景点", day2_places)  # 应该被挪到第2天，不是直接消失

    def test_schedule_trip_mode_clusters_nearby_candidates_onto_same_day(self):
        # 集成验证：两组分得很开的景点候选，排 2 天的行程时该各自成组，不会城中/路环拆开
        # 混进同一天——这是三步法（排名→聚类→槽位填充）真正接起来之后的端到端效果。
        # 每组给够 _ATTRACTIONS_PER_DAY_CLUSTER（4）个，不然第一天的聚类额度用不完会
        # 顺手跨区域多拿一个，那是"总量本来就不够分"时的合理退化，不是这条测试要验的东西
        from agents import route_agent
        import trip_plan as tp
        candidates = [
            {"place": f"城中景点{i}", "category": "景点", "lng": 113.54 + i * 0.001, "lat": 22.19 + i * 0.001, "recommendation_score": 0.9 - i * 0.01}
            for i in range(4)
        ] + [
            {"place": f"路环景点{i}", "category": "景点", "lng": 113.60 + i * 0.001, "lat": 22.10 + i * 0.001, "recommendation_score": 0.85 - i * 0.01}
            for i in range(4)
        ]
        state = {"trip_plan": tp.new_trip_plan("cluster-test")}
        fake_leg = {"mode": "walking", "duration_min": 5, "distance_m": 300}
        with patch.object(route_agent, "_real_leg", return_value=fake_leg), \
             patch.object(route_agent.llm_tool, "call_llm", return_value="[]"), \
             patch.object(route_agent.weather_tool, "get_hourly_forecast", return_value=[]):
            route_agent.schedule(state, candidates, city="澳门", mode="trip", time_budget_days=2)
        day1_places = {s["place"] for s in tp.day_stops(state["trip_plan"]["days"]["day-1"])}
        day2_places = {s["place"] for s in tp.day_stops(state["trip_plan"]["days"]["day-2"])}
        chengzhong = {f"城中景点{i}" for i in range(4)}
        louhuan = {f"路环景点{i}" for i in range(4)}
        # 每天真实排进 _SLOT_TEMPLATE 的景点槽位就 3 个（上午/下午/晚上，晚上非必选），核心是
        # 不能一天里同时出现两组地名——那才是"没顺路、瞎跳"的信号
        self.assertFalse(day1_places & chengzhong and day1_places & louhuan)
        self.assertFalse(day2_places & chengzhong and day2_places & louhuan)

    def test_fill_day_skeleton_respects_slot_windows_and_fills_anchors(self):
        from agents import route_agent
        attraction_pool = [
            {"place": "上午景点", "category": "景点", "lng": 113.54, "lat": 22.19},
            {"place": "下午景点", "category": "景点", "lng": 113.55, "lat": 22.20},
        ]
        breakfast_pool = [
            {"place": "早餐店", "category": "早餐", "lng": 113.539, "lat": 22.189},
        ]
        lunch_dinner_pool = [
            {"place": "午餐店", "category": "午晚餐", "lng": 113.541, "lat": 22.191},
            {"place": "晚餐店", "category": "午晚餐", "lng": 113.542, "lat": 22.192},
        ]
        fake_leg = {"mode": "walking", "duration_min": 5, "distance_m": 300}
        with patch.object(route_agent, "_real_leg", return_value=fake_leg):
            day_stops, left_attraction, left_breakfast, left_lunch_dinner = route_agent._fill_day_skeleton(
                attraction_pool, breakfast_pool, lunch_dinner_pool, "澳门"
            )
        slot_ids = {s["slot_id"] for s in day_stops}
        self.assertEqual({"breakfast", "morning", "lunch", "dinner"}, slot_ids & {"breakfast", "morning", "lunch", "dinner"})
        anchors = {s["slot_id"]: s["anchor"] for s in day_stops}
        self.assertTrue(anchors["breakfast"] and anchors["morning"] and anchors["lunch"] and anchors["dinner"])
        slot_end_by_id = {slot["slot_id"]: slot["end"] for slot in route_agent._SLOT_TEMPLATE}
        for stop in day_stops:
            self.assertLessEqual(stop["end_time"].strftime("%H:%M"), slot_end_by_id[stop["slot_id"]])
        self.assertEqual(len(day_stops) + len(left_attraction) + len(left_breakfast) + len(left_lunch_dinner), 5)

    def test_fill_day_skeleton_prioritizes_user_picked_over_nearer_candidate(self):
        # 2026-09-17：用户在 attraction_picker 里勾的候选该被优先排进去，哪怕同池子里有
        # 离上一站更近的候选——"优先"体现在槽位内候选试探顺序上，不是"只用这几个"
        from agents import route_agent
        breakfast_pool = [{"place": "早餐店", "category": "早餐", "lng": 113.539, "lat": 22.189}]
        attraction_pool = [
            {"place": "近但没被选中的景点", "category": "景点", "lng": 113.540, "lat": 22.190},
            {"place": "用户勾选的景点", "category": "景点", "lng": 113.60, "lat": 22.25},
        ]
        fake_leg = {"mode": "walking", "duration_min": 5, "distance_m": 300}
        with patch.object(route_agent, "_real_leg", return_value=fake_leg):
            day_stops, *_ = route_agent._fill_day_skeleton(
                attraction_pool, breakfast_pool, [], "澳门", priority_places={"用户勾选的景点"}
            )
        morning_stop = next(s for s in day_stops if s["slot_id"] == "morning")
        self.assertEqual(morning_stop["place"], "用户勾选的景点")

    def test_fill_day_skeleton_inserts_nearby_attraction(self):
        # "加塞"：景点槽位排完主候选后，槽位还剩足够时间、附近有很近的同角色候选就再排一个
        from agents import route_agent
        attraction_pool = [
            {"place": "主景点", "category": "景点", "lng": 113.54, "lat": 22.19},
            {"place": "近旁小景点", "category": "景点", "lng": 113.5401, "lat": 22.1901},
        ]
        fake_leg = {"mode": "walking", "duration_min": 2, "distance_m": 100}
        with patch.object(route_agent, "_real_leg", return_value=fake_leg):
            day_stops, _, _, _ = route_agent._fill_day_skeleton(attraction_pool, [], [], "澳门")
        morning_stops = [s for s in day_stops if s["slot_id"] == "morning"]
        self.assertEqual(len(morning_stops), 2)
        self.assertFalse(morning_stops[1]["anchor"])  # 加塞的不是锚点，_llm_review_day() 可以拿掉

    def test_fill_day_skeleton_afternoon_attraction_is_a_required_anchor(self):
        # 2026-09-17：候选配额是按"景点≥2×天数"筛的（content_agent.py），但排班这边如果只有
        # "上午"是必选锚点，"下午"排出来的候选 anchor=False，会被 _llm_review_day() 当成可
        # 拿掉的加塞项误删——候选池明明够、也真排出来了，复核一过又变回每天只保底1个景点。
        # "下午"改成必选锚点之后，只要 pick_one() 真排出来了，anchor 就必须是 True
        from agents import route_agent
        attraction_pool = [
            {"place": "上午景点", "category": "景点", "lng": 113.54, "lat": 22.19},
            {"place": "下午景点", "category": "景点", "lng": 113.55, "lat": 22.20},
        ]
        fake_leg = {"mode": "walking", "duration_min": 5, "distance_m": 300}
        with patch.object(route_agent, "_real_leg", return_value=fake_leg):
            day_stops, *_ = route_agent._fill_day_skeleton(attraction_pool, [], [], "澳门")
        afternoon_stop = next(s for s in day_stops if s["slot_id"] == "afternoon")
        self.assertTrue(afternoon_stop["anchor"])

    def test_llm_review_day_removes_flagged_non_anchor_indices(self):
        from agents import route_agent
        day_stops = [
            {"place": "甲", "category": "景点", "slot_id": "morning", "anchor": True, "arrival_time": "09:00", "end_time": "09:30", "lng": 113.54, "lat": 22.19},
            {"place": "乙(加塞)", "category": "景点", "slot_id": "morning", "anchor": False, "arrival_time": "09:35", "end_time": "10:05", "lng": 113.541, "lat": 22.191},
        ]
        with patch.object(route_agent.llm_tool, "call_llm", return_value="[1]"):
            dropped = route_agent._llm_review_day(day_stops)
        self.assertEqual(dropped, {1})

    def test_llm_review_day_payload_includes_recommendation_score(self):
        # 2026-09-16：候选的 recommendation_score（社区帖子来自 similarity_score，高德 POI
        # 来自 persona_match_score，content_agent.py 统一成这一个字段）之前只是算出来放着，
        # route_agent 从头到尾没读过——"删减"判断只能凭地点名字/类别/距离瞎猜。这次把它
        # 传进 _llm_review_day() 的复核 payload，让模型删减时有分可依
        from agents import route_agent
        day_stops = [
            {"place": "甲", "category": "景点", "slot_id": "morning", "anchor": True, "arrival_time": "09:00", "end_time": "09:30", "lng": 113.54, "lat": 22.19, "recommendation_score": 0.9},
            {"place": "乙(加塞)", "category": "景点", "slot_id": "morning", "anchor": False, "arrival_time": "09:35", "end_time": "10:05", "lng": 113.541, "lat": 22.191, "recommendation_score": 0.3},
        ]
        with patch.object(route_agent.llm_tool, "call_llm", return_value="[]") as mocked:
            route_agent._llm_review_day(day_stops)
        sent_payload = json.loads(mocked.call_args.args[0][1]["content"])
        self.assertEqual(sent_payload[0]["recommendation_score"], 0.3)

    def test_llm_review_day_skips_call_when_nothing_removable(self):
        # 一整天全是锚点（没有加塞/可选槽位候选）时，压根不用调 LLM
        from agents import route_agent
        day_stops = [
            {"place": "甲", "category": "景点", "slot_id": "morning", "anchor": True, "arrival_time": "09:00", "end_time": "09:30", "lng": 113.54, "lat": 22.19},
        ]
        with patch.object(route_agent.llm_tool, "call_llm") as mocked:
            dropped = route_agent._llm_review_day(day_stops)
        mocked.assert_not_called()
        self.assertEqual(dropped, set())

    def test_llm_review_day_degrades_gracefully_on_error(self):
        from agents import route_agent
        day_stops = [
            {"place": "乙(加塞)", "category": "景点", "slot_id": "morning", "anchor": False, "arrival_time": "09:35", "end_time": "10:05", "lng": 113.541, "lat": 22.191},
        ]
        with patch.object(route_agent.llm_tool, "call_llm", side_effect=RuntimeError("boom")):
            dropped = route_agent._llm_review_day(day_stops)
        self.assertEqual(dropped, set())

    def test_pick_seeds_with_quotas_enforces_per_day_minimums(self):
        # 2026-09-16：种子选择从"至少1个景点，剩下按相似度"改成硬性配额——景点>=2×天数，
        # 早餐>=天数，午晚餐>=2×天数（n天要吃2n顿午晚餐，候选池要有2n家店才能保证不撞店）
        from agents import content_agent
        sights = [{"place": f"景点{i}", "category": "景点", "similarity_score": 1 - i * 0.01} for i in range(5)]
        breakfasts = [{"place": f"早餐{i}", "category": "早餐", "similarity_score": 1 - i * 0.01} for i in range(5)]
        lunch_dinners = [{"place": f"午晚餐{i}", "category": "午晚餐", "similarity_score": 1 - i * 0.01} for i in range(5)]
        picked = content_agent._pick_seeds_with_quotas(sights + breakfasts + lunch_dinners, day_count=2)
        picked_by_cat = {}
        for p in picked:
            picked_by_cat.setdefault(p["category"], []).append(p["place"])
        self.assertEqual(len(picked_by_cat["景点"]), 4)  # 2×2
        self.assertEqual(len(picked_by_cat["早餐"]), 2)  # 1×2
        self.assertEqual(len(picked_by_cat["午晚餐"]), 4)  # 2×2
        # 每个类别内部按相似度取最高的几条（已经排好序，取到的应该是靠前那几个）
        self.assertEqual(picked_by_cat["景点"], ["景点0", "景点1", "景点2", "景点3"])

    def test_pick_seeds_with_quotas_falls_back_when_category_short(self):
        # 某个类别候选不够配额就照单全收，不强求，不报错
        from agents import content_agent
        posts = [
            {"place": "早餐1", "category": "早餐", "similarity_score": 0.9},
        ]
        picked = content_agent._pick_seeds_with_quotas(posts, day_count=3)  # 早餐配额需要3条，只有1条
        self.assertEqual(picked, posts)

    def test_classify_categories_applies_llm_corrections(self):
        # 2026-09-16：分类质检升级成三选一（景点/早餐/午晚餐），不再是"饮食/景点"二选一
        # ——社区帖子的 category 是人工录入的，可能真的标错，也可能只有粗粒度的"饮食"没细分
        # 早中晚；高德 POI 的 category 是原始 type 字符串，本身就不是干净的标签
        from agents import content_agent
        recs = [
            {"place": "甲餐厅", "category": "景点", "post_id": "p1"},
            {"place": "乙景点", "category": "景点"},
        ]
        with patch.object(content_agent.llm_tool, "call_llm", return_value='[{"index":0,"category":"午晚餐"}]'):
            content_agent._classify_categories(recs)
        self.assertEqual(recs[0]["category"], "午晚餐")
        self.assertEqual(recs[1]["category"], "景点")  # 没被模型点名的不动

    def test_classify_categories_writes_back_only_for_community_posts(self):
        from agents import content_agent
        recs = [
            {"place": "甲餐厅", "category": "景点", "post_id": "p1"},  # 社区帖子来源，带 post_id
            {"place": "乙餐厅", "category": "景点"},  # 高德 POI 来源，没有 post_id
        ]
        with patch.object(content_agent.llm_tool, "call_llm",
                           return_value='[{"index":0,"category":"早餐"},{"index":1,"category":"早餐"}]'), \
             patch.object(content_agent.store, "update_post_category") as update_mock:
            content_agent._classify_categories(recs)
        self.assertEqual(recs[0]["category"], "早餐")
        self.assertEqual(recs[1]["category"], "早餐")
        update_mock.assert_called_once_with("p1", "早餐")

    def test_classify_categories_degrades_gracefully_on_error(self):
        from agents import content_agent
        recs = [{"place": "甲餐厅", "category": "景点", "post_id": "p1"}]
        with patch.object(content_agent.llm_tool, "call_llm", side_effect=RuntimeError("boom")):
            content_agent._classify_categories(recs)
        self.assertEqual(recs[0]["category"], "景点")

    def test_extract_day_count_parses_or_defaults(self):
        from agents import content_agent
        self.assertEqual(content_agent._extract_day_count("推荐一个3天的行程"), 3)
        self.assertEqual(content_agent._extract_day_count("5日游怎么安排"), 5)
        self.assertEqual(content_agent._extract_day_count("推荐一下怎么玩"), 1)  # 提取不到默认1天
        self.assertEqual(content_agent._extract_day_count("来个99天的旅行"), 14)  # 封顶14天

    def test_extract_day_count_falls_back_to_date_range(self):
        # 2026-09-17 真实测试发现："我想在9.15号到9.18从北京去澳门玩"没有"N天"字样，
        # day_count 悄悄退到默认值 1，连带把候选 fetch 数量/硬性类别配额都缩没了，社区库里
        # 本来就有限的几条"景点"帖子排名不够靠前直接被挤出候选池，最后排出来的候选全是
        # 餐饮——不是排班逻辑的锅，是天数提取漏判了日期区间这种没有显式"N天"的表达。
        # 用户随后又指出：9.15到9.18是4天（15/16/17/18号都要算），不是日期差算出来的3——
        # 日期差是"晚数"，天数要在这基础上 +1
        from agents import content_agent
        self.assertEqual(content_agent._extract_day_count("我想在9.15号到9.18从北京去澳门玩，帮我看看怎么玩呗"), 4)
        self.assertEqual(content_agent._extract_day_count("9月15日到9月18日去澳门"), 4)  # "15日"不能被误判成15天
        self.assertEqual(content_agent._extract_day_count("9/15-9/18去香港"), 4)
        self.assertEqual(content_agent._extract_day_count("推荐一个3天的行程，9.1到9.5出发"), 3)  # 显式天数优先于日期区间

    def test_days_last_stops_takes_final_geocoded_node_per_day(self):
        # 2026-09-15：晚上回酒店睡觉，"最后一站"比"第一站"更直接影响住宿体验，酒店锚点/
        # 距离复核都基于每天最后一站，不是第一站
        import trip_plan
        from agents import ota_hotel_agent
        trip = trip_plan.new_trip_plan("test-last-stops")
        day1 = trip_plan.get_or_create_day(trip, "2026-09-15")
        trip_plan.add_stop(day1, "n1", "attraction", "大三巴", arrival_transport="首站", arrival_time="09:00", end_time="10:00", lng=113.54, lat=22.19)
        trip_plan.add_stop(day1, "n2", "meal", "晚餐店", arrival_transport="步行", arrival_time="18:00", end_time="19:00", after_id="n1", lng=113.545, lat=22.195)
        last_stops = ota_hotel_agent._days_last_stops(trip)
        self.assertEqual(len(last_stops), 1)
        self.assertEqual(last_stops[0]["place"], "晚餐店")

    def test_anchor_place_from_trip_uses_multi_day_centroid(self):
        # 2026-09-15：多天行程的锚点改成"所有天数最后一站坐标的重心"，不是只看某一天——
        # 不然锚点只照顾了 Day1，Day3 跑去别的区域完全没考虑到
        import trip_plan
        from agents import ota_hotel_agent
        trip = trip_plan.new_trip_plan("test-anchor-centroid")
        day1 = trip_plan.get_or_create_day(trip, "2026-09-15")
        trip_plan.add_stop(day1, "n1", "attraction", "大三巴", arrival_transport="首站", arrival_time="09:00", end_time="10:00", lng=113.54, lat=22.19)
        day2 = trip_plan.get_or_create_day(trip, "2026-09-16")
        trip_plan.add_stop(day2, "n1", "attraction", "黑沙环", arrival_transport="首站", arrival_time="09:00", end_time="10:00", lng=113.55, lat=22.20)
        anchor = ota_hotel_agent._anchor_place_from_trip(trip)
        self.assertAlmostEqual(anchor["lng"], (113.54 + 113.55) / 2)
        self.assertAlmostEqual(anchor["lat"], (22.19 + 22.20) / 2)
        self.assertIn(anchor["place"], ("大三巴", "黑沙环"))  # 离重心最近的那一站

    def test_existing_hotel_distance_check_flags_far_days(self):
        import trip_plan
        from agents import ota_hotel_agent
        trip = trip_plan.new_trip_plan("test-hotel-distance")
        trip_plan.add_hotel(trip, {"name": "近城酒店", "lng": 113.54, "lat": 22.19})
        day1 = trip_plan.get_or_create_day(trip, "2026-09-15")
        trip_plan.add_stop(day1, "n1", "attraction", "近处景点", arrival_transport="首站", arrival_time="09:00", end_time="10:00", lng=113.541, lat=22.191)
        day2 = trip_plan.get_or_create_day(trip, "2026-09-16")
        trip_plan.add_stop(day2, "n1", "attraction", "远处景点", arrival_transport="首站", arrival_time="09:00", end_time="10:00", lng=113.7, lat=22.35)  # 明显远
        reminder = ota_hotel_agent._existing_hotel_distance_check(trip)
        self.assertIsNotNone(reminder)
        self.assertIn("2026-09-16", reminder)
        self.assertIn("远处景点", reminder)
        self.assertNotIn("2026-09-15", reminder)

    def test_existing_hotel_distance_check_returns_none_when_all_close(self):
        import trip_plan
        from agents import ota_hotel_agent
        trip = trip_plan.new_trip_plan("test-hotel-distance-close")
        trip_plan.add_hotel(trip, {"name": "近城酒店", "lng": 113.54, "lat": 22.19})
        day1 = trip_plan.get_or_create_day(trip, "2026-09-15")
        trip_plan.add_stop(day1, "n1", "attraction", "近处景点", arrival_transport="首站", arrival_time="09:00", end_time="10:00", lng=113.541, lat=22.191)
        self.assertIsNone(ota_hotel_agent._existing_hotel_distance_check(trip))

    def test_existing_hotel_distance_check_skips_hotel_without_coordinates(self):
        import trip_plan
        from agents import ota_hotel_agent
        trip = trip_plan.new_trip_plan("test-hotel-distance-no-coords")
        trip_plan.add_hotel(trip, {"name": "老数据酒店"})  # 坐标透传修好之前订的老数据，没有 lng/lat
        day1 = trip_plan.get_or_create_day(trip, "2026-09-15")
        trip_plan.add_stop(day1, "n1", "attraction", "某景点", arrival_transport="首站", arrival_time="09:00", end_time="10:00", lng=113.7, lat=22.35)
        self.assertIsNone(ota_hotel_agent._existing_hotel_distance_check(trip))

    def test_run_skips_hotel_search_when_already_booked(self):
        # 已经订好酒店是用户确认过的真实决定，不该每次 booking 意图命中都重新搜一遍候选
        # 去"暗示"换酒店
        import trip_plan
        from agents import ota_hotel_agent
        trip = trip_plan.new_trip_plan("test-run-skip-search")
        trip_plan.add_hotel(trip, {"name": "已订酒店", "lng": 113.54, "lat": 22.19})
        state = {"trip_plan": trip, "city": "澳门"}
        with patch.object(ota_hotel_agent, "_search_real_hotels") as search_mock:
            result = ota_hotel_agent.run(state, location=None, date_range="2026-09-15~2026-09-17")
        search_mock.assert_not_called()
        self.assertFalse(any(c.get("provider_type") == "hotel" for c in result["candidates"]))

    def test_anchor_place_from_trip_returns_none_when_no_stops(self):
        import trip_plan
        from agents import ota_hotel_agent
        trip = trip_plan.new_trip_plan("test-anchor-place-empty")
        self.assertIsNone(ota_hotel_agent._anchor_place_from_trip(trip))
        self.assertIsNone(ota_hotel_agent._anchor_place_from_trip({}))

    def test_search_real_hotels_narrows_by_anchor_place(self):
        from agents import ota_hotel_agent
        anchor = {"place": "大三巴", "lng": 113.54, "lat": 22.19}
        on_target_hotel = [{"name": "近旁酒店", "lng": 113.541, "lat": 22.191}]
        with patch.object(ota_hotel_agent.hotel_tool, "search_hotels", return_value=on_target_hotel) as mocked, \
             patch.object(ota_hotel_agent, "_city_center", return_value={"lng": 113.54, "lat": 22.19}):
            ota_hotel_agent._search_real_hotels("澳门", None, None, 2, None, None, anchor=anchor)
        self.assertEqual(mocked.call_args.kwargs["place"], "大三巴")
        self.assertEqual(mocked.call_args.kwargs["place_type"], "景点")

    def test_search_real_hotels_falls_back_to_city_without_anchor(self):
        from agents import ota_hotel_agent
        with patch.object(ota_hotel_agent.hotel_tool, "search_hotels", return_value=[]) as mocked, \
             patch.object(ota_hotel_agent, "_city_center", return_value={"lng": 113.54, "lat": 22.19}):
            ota_hotel_agent._search_real_hotels("澳门", None, None, 2, None, None, anchor=None)
        self.assertEqual(mocked.call_args.kwargs["place"], "澳门")
        self.assertEqual(mocked.call_args.kwargs["place_type"], "城市")

    def test_search_real_hotels_retries_city_wide_when_anchor_match_is_way_off(self):
        # 2026-09-15：真实踩过的坑——RollingGo 把"中西药局旧址"这种没那么出名的地标匹配到
        # 美国圣路易斯去了（坐标直接跑去密苏里州）。锚点搜索结果离已知真实坐标太远时要整批
        # 放弃，退回城市级搜索重查一次，不能把跑偏的结果直接返回给用户
        from agents import ota_hotel_agent
        anchor = {"place": "中西药局旧址", "lng": 113.54, "lat": 22.19}
        far_away_hotel = [{"name": "圣路易斯威斯汀酒店", "lng": -90.195015, "lat": 38.623196}]
        on_target_hotel = [{"name": "近旁酒店", "lng": 113.541, "lat": 22.191}]
        with patch.object(
            ota_hotel_agent.hotel_tool, "search_hotels", side_effect=[far_away_hotel, on_target_hotel]
        ) as mocked, patch.object(ota_hotel_agent, "_city_center", return_value={"lng": 113.54, "lat": 22.19}):
            candidates, error = ota_hotel_agent._search_real_hotels("澳门", None, None, 2, None, None, anchor=anchor)
        self.assertIsNone(error)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["name"], "近旁酒店")
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(mocked.call_args_list[1].kwargs["place"], "澳门")
        self.assertEqual(mocked.call_args_list[1].kwargs["place_type"], "城市")

    def test_search_real_hotels_keeps_only_on_target_when_results_are_mixed(self):
        # 2026-09-17：用户真实预订时反馈"地图上出现了异地酒店"——查出根因是 on_target 之前
        # 只用来判断"要不要整批重查"，判断完之后却没拿过滤后的结果替换 hotels：5 个候选里
        # 有 1 个在范围内就不会触发重查，但混进最终候选的还是原始未过滤的那一批，跑偏的
        # 酒店会跟着正常的一起冒出来。现在必须验证：命中 on_target 时确实只保留通过校验的
        from agents import ota_hotel_agent
        anchor = {"place": "大三巴", "lng": 113.54, "lat": 22.19}
        mixed_hotels = [
            {"name": "近旁酒店", "lng": 113.541, "lat": 22.191},
            {"name": "异地酒店", "lng": -90.195015, "lat": 38.623196},
        ]
        with patch.object(ota_hotel_agent.hotel_tool, "search_hotels", return_value=mixed_hotels) as mocked, \
             patch.object(ota_hotel_agent, "_city_center", return_value={"lng": 113.54, "lat": 22.19}):
            candidates, error = ota_hotel_agent._search_real_hotels("澳门", None, None, 2, None, None, anchor=anchor)
        self.assertIsNone(error)
        self.assertEqual([c["name"] for c in candidates], ["近旁酒店"])
        self.assertEqual(mocked.call_count, 1)  # 有 on_target 结果，不该触发城市级重查

    def test_search_real_hotels_city_level_sanity_check_without_anchor(self):
        # 没有 anchor 的城市级搜索之前完全没有任何坐标合理性校验——查到别的城市/国家去也会
        # 原样返回给用户。现在不管有没有 anchor 都要过一次"离城市中心够不够近"的兜底校验
        from agents import ota_hotel_agent
        mixed_hotels = [
            {"name": "本地酒店", "lng": 113.541, "lat": 22.191},
            {"name": "异地酒店", "lng": -90.195015, "lat": 38.623196},
        ]
        with patch.object(ota_hotel_agent.hotel_tool, "search_hotels", return_value=mixed_hotels), \
             patch.object(ota_hotel_agent, "_city_center", return_value={"lng": 113.54, "lat": 22.19}):
            candidates, error = ota_hotel_agent._search_real_hotels("澳门", None, None, 2, None, None, anchor=None)
        self.assertIsNone(error)
        self.assertEqual([c["name"] for c in candidates], ["本地酒店"])

    def test_search_real_hotels_skips_sanity_check_when_city_center_unresolvable(self):
        # _city_center() 查不到（城市名字打错/API 抽风）就跳过这层校验，不强求、不报错
        from agents import ota_hotel_agent
        hotels = [{"name": "某酒店", "lng": 113.541, "lat": 22.191}]
        with patch.object(ota_hotel_agent.hotel_tool, "search_hotels", return_value=hotels), \
             patch.object(ota_hotel_agent, "_city_center", return_value=None):
            candidates, error = ota_hotel_agent._search_real_hotels("澳门", None, None, 2, None, None, anchor=None)
        self.assertIsNone(error)
        self.assertEqual(len(candidates), 1)

    def test_search_real_hotels_forwards_star_min(self):
        # 2026-09-16：star_min 是 hotel_tool.search_hotels() 本来就有的参数，之前
        # _search_real_hotels() 没接上——trip_preferences.py 的"度假酒店"场景要用它
        # 提高星级门槛
        from agents import ota_hotel_agent
        with patch.object(ota_hotel_agent.hotel_tool, "search_hotels", return_value=[]) as mocked, \
             patch.object(ota_hotel_agent, "_city_center", return_value={"lng": 113.54, "lat": 22.19}):
            ota_hotel_agent._search_real_hotels("澳门", None, None, 2, None, None, anchor=None, star_min=4.0)
        self.assertEqual(mocked.call_args.kwargs["star_min"], 4.0)

    def test_run_hotel_preference_resort_skips_anchor_and_raises_star_min(self):
        # 2026-09-16：hotel_preference="度假酒店" 时不走锚点收窄（用户不介意离行程远，
        # 想单独挑一家好酒店），提高星级门槛偏向更好的酒店
        import trip_plan
        from agents import ota_hotel_agent
        trip = trip_plan.new_trip_plan("test-hotel-preference-resort")
        day1 = trip_plan.get_or_create_day(trip, "2026-09-15")
        trip_plan.add_stop(day1, "n1", "attraction", "大三巴", arrival_transport="首站", arrival_time="09:00", end_time="10:00", lng=113.54, lat=22.19)
        state = {"trip_plan": trip, "city": "澳门", "trip_preferences": {"hotel_preference": "度假酒店", "flight_priority": "折衷"}}
        with patch.object(ota_hotel_agent, "_search_real_hotels", return_value=([], None)) as search_mock, \
             patch.object(ota_hotel_agent, "_anchor_place_from_trip") as anchor_mock:
            ota_hotel_agent.run(state, location=None, date_range="2026-09-20~2026-09-22")
        anchor_mock.assert_not_called()  # 不该去算锚点，压根用不上
        self.assertIsNone(search_mock.call_args.kwargs["anchor"])
        self.assertEqual(search_mock.call_args.kwargs["star_min"], 4.0)

    def test_run_sorts_flights_by_price_when_saving_money(self):
        from agents import ota_hotel_agent
        state = {"trip_plan": {"days": {}, "hotels": []}, "city": "澳门", "trip_preferences": {"hotel_preference": "周边", "flight_priority": "省钱"}}
        with patch.object(ota_hotel_agent, "_search_real_hotels", return_value=([], None)):
            result = ota_hotel_agent.run(state, location=None, date_range="2026-09-20~2026-09-22")
        flight_prices = [c["price"] for c in result["candidates"] if c.get("provider_type") == "flight"]
        self.assertEqual(flight_prices, sorted(flight_prices))

    def test_trip_preferences_missing_fields_and_defaults(self):
        import trip_preferences
        prefs = trip_preferences.TripPreferences()
        self.assertEqual(set(prefs.missing_fields()), {"hotel_preference", "flight_priority"})
        self.assertFalse(prefs.is_complete())
        prefs.hotel_preference = "度假酒店"
        self.assertEqual(prefs.missing_fields(), ["flight_priority"])
        prefs.apply_defaults()
        self.assertTrue(prefs.is_complete())
        self.assertEqual(prefs.flight_priority, "折衷")  # 应用默认值，酒店偏好保留原值不覆盖
        self.assertEqual(prefs.hotel_preference, "度假酒店")

    def test_trip_preferences_from_dict_rejects_invalid_values(self):
        import trip_preferences
        restored = trip_preferences.TripPreferences.from_dict({"hotel_preference": "不存在的值", "flight_priority": "省钱"})
        self.assertIsNone(restored.hotel_preference)  # 非法值当没填
        self.assertEqual(restored.flight_priority, "省钱")
        self.assertEqual(trip_preferences.TripPreferences.from_dict(None).to_dict(), {"hotel_preference": None, "flight_priority": None})

    def test_build_preference_question_lists_only_missing_fields(self):
        from agents import orchestrator_agent as agent
        question = agent._build_preference_question(["hotel_preference"])
        self.assertIn("酒店", question)
        self.assertNotIn("机票", question)
        self.assertIn("继续", question)  # 提示可以跳过

if __name__ == "__main__": unittest.main()
