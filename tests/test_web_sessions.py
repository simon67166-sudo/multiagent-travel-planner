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
        self.assertNotIn("test hotel", json.dumps(self.store.load(sid)["trip_plan"]))
        proposal = response.json["proposal"]
        self.assertIsNotNone(proposal)
        accepted = self.a.post("/proposals/"+proposal["id"]+"/accept",json={"expected_version":proposal["base_version"]})
        self.assertEqual(accepted.status_code,200,accepted.json)
        self.assertEqual(self.a.post("/widget-response", json={"widget": "hotel_picker", "selected": [item]}).status_code, 400)
        self.assertNotIn("test hotel", json.dumps(self.b.get("/trip").json))
        self.assertIn("test hotel", json.dumps(self.a.get("/trip").json))

    def test_nearby_plan_persists_without_overwriting_legacy_trip(self):
        plan={"request":{"city":"香港"},"stops":[],"weather":{},"reminders":[]}
        with patch("nearby_planner.build_plan",return_value=plan), patch("nearby_planner.plan_reply",return_value="nearby plan"):
            response=self.a.post("/nearby-plan",json={"city":"香港"})
        self.assertEqual(response.status_code,200)
        self.assertNotIn("nearby_plan",self.a.get("/session").json)
        proposal=response.json["proposal"]
        self.assertEqual(proposal["itinerary"]["nearby_plan"],plan)
        self.assertEqual(self.a.post("/proposals/"+proposal["id"]+"/accept",json={"expected_version":0}).status_code,200)
        self.assertEqual(self.a.get("/session").json["nearby_plan"],plan)
        self.assertNotIn("nearby_plan",self.b.get("/session").json)

    def test_nearby_bad_request(self):
        self.assertEqual(self.a.post("/nearby-plan",json={"city":"杭州"}).status_code,400)
        self.assertEqual(self.a.post("/nearby-weather",json={}).status_code,400)

    def test_index_still_loads(self):
        self.assertEqual(self.a.get("/").status_code, 200)

class OrchestrationTests(unittest.TestCase):
    def test_restaurant_uses_history_without_adding_mock_map_stop(self):
        from agents import orchestrator_agent as agent
        state = agent.new_shared_state("x")
        state["messages"] = [{"role": "user", "content": "previous preference"}, {"role": "assistant", "content": "ok"}]
        result = {"reply": "mock Macau", "is_mock": True, "evidence": []}
        with patch.object(agent.llm_tool, "call_llm", return_value='["restaurant", "route"]') as classify, patch.object(agent.restaurant_agent, "run", return_value=result), patch.object(agent.route_agent, "run") as route:
            output, _ = agent.orchestrate("budget changed", state)
        self.assertIn("mock Macau", output["chat_reply"])
        self.assertIn("previous preference", json.dumps(classify.call_args_list[0].args[0]))
        route.assert_not_called()

if __name__ == "__main__":
    unittest.main()
