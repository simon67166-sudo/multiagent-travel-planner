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

if __name__ == "__main__":
    unittest.main()
