import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))

class WebTests(unittest.TestCase):
    def setUp(self):
        import server
        from session_store import SessionStore
        self.server = server
        self.temp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self.temp.name) / "sessions.db")
        self.patcher = patch.object(server, "session_store", self.store, create=True)
        self.patcher.start()
        server.app.config["TESTING"] = True
        self.a = server.app.test_client()
        self.b = server.app.test_client()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def fake(self, text, state):
        state.setdefault("messages", []).extend([{"role": "user", "content": text}, {"role": "assistant", "content": "saved"}])
        return {"chat_reply": "saved", "widgets": [], "map_panel": {}, "community_panel": []}, state

    def test_history_isolated_and_restored_from_new_store(self):
        from session_store import SessionStore
        with patch.object(self.server.main, "orchestrate", side_effect=self.fake):
            self.assertEqual(self.a.post("/chat", json={"message": "budget 80"}).status_code, 200)
        self.assertEqual(self.b.get("/session").json["messages"], [])
        with patch.object(self.server, "session_store", SessionStore(self.store.path)):
            self.assertEqual(self.a.get("/session").json["messages"][0]["content"], "budget 80")

    def test_bad_payloads(self):
        for payload in ([], {"message": 123}, {"message": ""}):
            self.assertEqual(self.a.post("/chat", json=payload).status_code, 400)

    def test_failed_turn_rolls_back(self):
        self.a.get("/session")
        def fail(text, state):
            state["messages"] = [{"role": "user", "content": "partial"}]
            raise RuntimeError("private provider details")
        with patch.object(self.server.main, "orchestrate", side_effect=fail):
            response = self.a.post("/chat", json={"message": "hello"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private provider", response.get_data(as_text=True))
        self.assertEqual(self.a.get("/session").json["messages"], [])

    def test_reject_unoffered_widget(self):
        response = self.a.post("/widget-response", json={"widget": "hotel_picker", "selected": [{"name": "invented"}]})
        self.assertEqual(response.status_code, 400)

    def test_valid_widget_persists_trip_and_cannot_replay(self):
        self.a.get("/session")
        sid = self.a.get_cookie("travel_session").value
        item = {"name": "test hotel", "price": 80}
        with self.store.edit(sid, dict) as state:
            state["pending_widgets"] = [{"widget": "hotel_picker", "data": {"options": [item], "max_select": 1}}]
        response = self.a.post("/widget-response", json={"widget": "hotel_picker", "selected": [item]})
        self.assertEqual(response.status_code, 200)
        self.assertIn("test hotel", json.dumps(self.store.load(sid)["trip_plan"]))
        self.assertEqual(self.a.post("/widget-response", json={"widget": "hotel_picker", "selected": [item]}).status_code, 400)
        self.assertNotIn("test hotel", json.dumps(self.b.get("/trip").json))
        self.assertIn("test hotel", json.dumps(self.a.get("/trip").json))

    def test_attraction_picker_confirm_triggers_real_schedule(self):
        # 2026-09-15：排时间的触发点从"意图分类猜中了 route"改成"用户在 attraction_picker
        # 选完确认"——这里验证确认动作真的走到了 route_agent.schedule()（骨架排班，不是
        # 已删除的老接口 run()），而且用的是候选自带的真实坐标，不会重新地理编码
        import map_tool
        self.a.get("/session")
        sid = self.a.get_cookie("travel_session").value
        item = {"place": "大三巴牌坊", "category": "景点", "lng": 113.54, "lat": 22.19, "source": "社区帖子"}
        with self.store.edit(sid, dict) as state:
            state["pending_widgets"] = [{"widget": "attraction_picker", "data": {"options": [item], "max_select": 6}}]
        with patch.object(map_tool, "geocode", side_effect=AssertionError("不该调用 geocode()")):
            response = self.a.post("/widget-response", json={"widget": "attraction_picker", "selected": [item]})
        self.assertEqual(response.status_code, 200)
        trip = self.store.load(sid)["trip_plan"]
        self.assertIn("day-1", trip["days"])
        self.assertIn("大三巴牌坊", json.dumps(trip, ensure_ascii=False))

    def test_attraction_picker_confirm_uses_full_candidate_pool_not_just_picks(self):
        # 2026-09-17：用户指出排班不该只看用户勾选的那几个——那样一来用户没勾的早餐/午晚餐
        # 槽位完全没有候选可用，只能空着。达人 Agent 的全量候选池存在
        # state["last_content_candidates"]，这里只勾了一个景点，但全量池里还有早餐/午餐候选，
        # 排完之后 day-1 应该也有早餐/午餐被排进去，不是只有用户勾的那个景点
        picked_sight = {"place": "大三巴牌坊", "category": "景点", "lng": 113.54, "lat": 22.19, "source": "社区帖子"}
        breakfast_candidate = {"place": "盛记白粥", "category": "早餐", "lng": 113.539, "lat": 22.189, "source": "社区帖子"}
        lunch_candidate = {"place": "叠记咖喱美食", "category": "午晚餐", "lng": 113.541, "lat": 22.191, "source": "社区帖子"}
        self.a.get("/session")
        sid = self.a.get_cookie("travel_session").value
        with self.store.edit(sid, dict) as state:
            state["last_content_candidates"] = [picked_sight, breakfast_candidate, lunch_candidate]
            state["pending_widgets"] = [{"widget": "attraction_picker", "data": {"options": [picked_sight], "max_select": 6}}]
        response = self.a.post("/widget-response", json={"widget": "attraction_picker", "selected": [picked_sight]})
        self.assertEqual(response.status_code, 200)
        trip = self.store.load(sid)["trip_plan"]
        day1_places = json.dumps(trip["days"]["day-1"], ensure_ascii=False)
        self.assertIn("大三巴牌坊", day1_places)
        self.assertIn("盛记白粥", day1_places)  # 用户没勾选，但该从全量池里自动补进早餐槽位
        self.assertIn("叠记咖喱美食", day1_places)  # 同理，午晚餐槽位

    def test_flight_picker_confirm_persists_date(self):
        # 2026-09-17：机票候选（ota_hotel_agent.run() 算的）本来就带 "date" 字段，但确认时
        # 存进 trip_plan.flights 的字典手动拼字段漏了这一项，前端机票卡片自然显示不出日期——
        # 不是前端没做，是数据在这里断了
        item = {"flight_no": "MU5137", "from_": "北京首都国际机场", "to": "澳门",
                "date": "2026-09-15", "depart_time": "08:00", "arrive_time": "10:10", "status": "on_time"}
        self.a.get("/session")
        sid = self.a.get_cookie("travel_session").value
        with self.store.edit(sid, dict) as state:
            state["pending_widgets"] = [{"widget": "flight_picker", "data": {"options": [item], "max_select": 1}}]
        response = self.a.post("/widget-response", json={"widget": "flight_picker", "selected": [item]})
        self.assertEqual(response.status_code, 200)
        flights = self.store.load(sid)["trip_plan"]["flights"]
        self.assertEqual(flights[0]["date"], "2026-09-15")

    def test_attraction_picker_confirm_schedules_all_requested_days_at_once(self):
        # 2026-09-17：用户反馈"说了3天的行程，确认一次却只排出1天"——之前 time_budget_days
        # 写死 1，得确认 3 次才能凑够 3 天。天数是达人 Agent 提取到的
        # （content_agent._extract_day_count()，编排 Agent 存进
        # state["last_trip_day_count"]），确认一次就该照这个天数把行程排够，不用分好几轮
        candidates = []
        for i in range(3):
            candidates.append({"place": f"景点{i}", "category": "景点", "lng": 113.54 + i * 0.01, "lat": 22.19, "source": "社区帖子"})
            candidates.append({"place": f"早餐{i}", "category": "早餐", "lng": 113.539 + i * 0.01, "lat": 22.189, "source": "社区帖子"})
            candidates.append({"place": f"午晚餐{i}", "category": "午晚餐", "lng": 113.541 + i * 0.01, "lat": 22.191, "source": "社区帖子"})
        picked_sight = candidates[0]
        self.a.get("/session")
        sid = self.a.get_cookie("travel_session").value
        with self.store.edit(sid, dict) as state:
            state["last_content_candidates"] = candidates
            state["last_trip_day_count"] = 3
            state["pending_widgets"] = [{"widget": "attraction_picker", "data": {"options": [picked_sight], "max_select": 6}}]
        response = self.a.post("/widget-response", json={"widget": "attraction_picker", "selected": [picked_sight]})
        self.assertEqual(response.status_code, 200)
        trip = self.store.load(sid)["trip_plan"]
        self.assertEqual(set(trip["days"].keys()), {"day-1", "day-2", "day-3"})

    def test_attraction_picker_confirm_adds_bridging_chat_reply(self):
        # 2026-09-17：用户反馈"做完交互后能不能加点衔接提示"——选完确认之前只更新
        # trip_plan/地图面板，聊天框毫无反应；现在 apply_selection() 生成一句衔接回复，
        # 既存进 state["messages"]（下次 /session 刷新还在）也直接回传给前端这次响应用
        item = {"place": "大三巴牌坊", "category": "景点", "lng": 113.54, "lat": 22.19, "source": "社区帖子"}
        self.a.get("/session")
        sid = self.a.get_cookie("travel_session").value
        with self.store.edit(sid, dict) as state:
            state["pending_widgets"] = [{"widget": "attraction_picker", "data": {"options": [item], "max_select": 6}}]
        response = self.a.post("/widget-response", json={"widget": "attraction_picker", "selected": [item]})
        self.assertIn("大三巴牌坊", response.json["chat_reply"])
        self.assertIn("day-1", response.json["chat_reply"])
        messages = self.store.load(sid)["messages"]
        self.assertEqual(messages[-1], {"role": "assistant", "content": response.json["chat_reply"]})

    def test_hotel_picker_confirm_reply_includes_distance_reminder(self):
        # 酒店确认之后的衔接回复该带上"离行程终点比较远"的提醒（ota_hotel_agent 已有的
        # _existing_hotel_distance_check() 逻辑，之前只有聊天流程接了，widget 流程没接）
        import trip_plan as trip_plan_module
        far_hotel = {"name": "氹仔某酒店", "price": 500, "lng": 113.56, "lat": 22.05}  # 距大三巴 ~16km，超出 5km 提醒阈值
        self.a.get("/session")
        sid = self.a.get_cookie("travel_session").value
        with self.store.edit(sid, dict) as state:
            day1 = trip_plan_module.get_or_create_day(state["trip_plan"], "2026-09-15")
            trip_plan_module.add_stop(day1, "n1", "attraction", "大三巴牌坊", arrival_transport="首站",
                                       arrival_time="09:00", end_time="10:00", lng=113.54, lat=22.19)
            state["pending_widgets"] = [{"widget": "hotel_picker", "data": {"options": [far_hotel], "max_select": 1}}]
        response = self.a.post("/widget-response", json={"widget": "hotel_picker", "selected": [far_hotel]})
        self.assertIn("氹仔某酒店", response.json["chat_reply"])
        self.assertIn("比较远", response.json["chat_reply"])

    def test_nearby_endpoints_removed(self):
        # 2026-09-14：nearby 并入了达人 Agent（content_agent.run(mode="nearby") +
        # route_agent.schedule()），走 POST /chat 就行。这三个独立端点连同独立表单前端
        # 一起整个删掉了（不是先前那版短暂过渡期的 501 占位），路由已经不存在，404
        self.assertEqual(self.a.post("/nearby-plan", json={"city": "香港"}).status_code, 404)
        self.assertEqual(self.a.post("/nearby-weather", json={}).status_code, 404)

    def test_index_still_loads(self):
        self.assertEqual(self.a.get("/").status_code, 200)

class OrchestrationTests(unittest.TestCase):
    def test_restaurant_uses_history_without_adding_mock_map_stop(self):
        from agents import orchestrator_agent as agent
        state = agent.new_shared_state("x")
        state["messages"] = [{"role": "user", "content": "previous preference"}, {"role": "assistant", "content": "ok"}]
        result = {"reply": "mock Macau", "is_mock": True, "evidence": []}
        with patch.object(agent.llm_tool, "call_llm", return_value='["restaurant"]') as classify, patch.object(agent.restaurant_agent, "run", return_value=result), patch.object(agent.route_agent, "schedule") as schedule:
            output, _ = agent.orchestrate("budget changed", state)
        self.assertIn("mock Macau", output["chat_reply"])
        self.assertIn("previous preference", json.dumps(classify.call_args_list[0].args[0]))
        schedule.assert_not_called()

    def test_content_intent_gates_on_trip_preferences_then_resumes_original_message(self):
        # 2026-09-16：常规推荐（mode="trip"）开始搜索前先确认酒店/机票偏好（persona 系统
        # 已经覆盖的"节奏"/"模式"不重复问）。第一轮应该只问问题、不调 content_agent；
        # 第二轮（哪怕用户直接说"继续"）应该用第一轮暂存的原始请求继续走正常流程
        from agents import orchestrator_agent as agent
        import trip_preferences
        state = agent.new_shared_state("preference-gate-test-user")

        with patch.object(agent, "classify_intent", return_value=["content"]), \
             patch.object(agent.content_agent, "run") as content_mock:
            output, state = agent.orchestrate("推荐一个3天的行程", state)
        content_mock.assert_not_called()
        self.assertIn("酒店", output["chat_reply"])
        self.assertEqual(state["pending_trip_request"], "推荐一个3天的行程")
        self.assertFalse(trip_preferences.TripPreferences.from_dict(state["trip_preferences"]).is_complete())

        fake_content_result = {"recommendations": [], "nearby_params": None, "clarification_needed": None}
        with patch.object(agent, "classify_intent", return_value=["content"]), \
             patch.object(agent, "_parse_preference_answer", return_value={}), \
             patch.object(agent.content_agent, "run", return_value=fake_content_result) as content_mock, \
             patch.object(agent.llm_tool, "call_llm", return_value="收到"):
            agent.orchestrate("继续", state)
        content_mock.assert_called_once()
        self.assertEqual(content_mock.call_args.kwargs["location_hint"], "推荐一个3天的行程")
        self.assertIsNone(state.get("pending_trip_request"))
        self.assertTrue(trip_preferences.TripPreferences.from_dict(state["trip_preferences"]).is_complete())

    def test_trip_preferences_reasked_when_destination_city_changes(self):
        # 2026-09-17：用户指出酒店/机票偏好是"这一趟去哪儿"的定制，不是绑在人身上的人格
        # 变量——"去福州可能更想吃，去香港可能更想打卡，去岘港可能想度假，每次都不一样"。
        # 第一轮在澳门问完、答完；第二轮换成香港，该被当成全新一趟行程重新问一次，不能
        # 沿用澳门那次的答案
        from agents import orchestrator_agent as agent
        import trip_preferences
        state = agent.new_shared_state("city-change-test-user")

        with patch.object(agent, "classify_intent", return_value=["content"]):
            agent.orchestrate("推荐一个3天的澳门行程", state)
        with patch.object(agent, "classify_intent", return_value=["content"]), \
             patch.object(agent, "_parse_preference_answer", return_value={"hotel_preference": "度假酒店", "flight_priority": "省钱"}), \
             patch.object(agent.content_agent, "run", return_value={"recommendations": [], "nearby_params": None, "clarification_needed": None}), \
             patch.object(agent.llm_tool, "call_llm", return_value="收到"):
            agent.orchestrate("周边方便，省钱", state)
        macau_prefs = trip_preferences.TripPreferences.from_dict(state["trip_preferences"])
        self.assertTrue(macau_prefs.is_complete())
        self.assertEqual(macau_prefs.hotel_preference, "度假酒店")
        self.assertEqual(state["trip_preferences_city"], "澳门")

        # 换成香港——该重新触发确认，不是直接沿用澳门那次"度假酒店/省钱"的答案
        with patch.object(agent, "classify_intent", return_value=["content"]), \
             patch.object(agent.content_agent, "run") as content_mock:
            output, state = agent.orchestrate("推荐一个香港的行程", state)
        content_mock.assert_not_called()
        self.assertIn("酒店", output["chat_reply"])
        self.assertFalse(trip_preferences.TripPreferences.from_dict(state["trip_preferences"]).is_complete())
        self.assertEqual(state["pending_trip_request"], "推荐一个香港的行程")
        self.assertEqual(state["pending_trip_city"], "香港")

if __name__ == "__main__":
    unittest.main()
