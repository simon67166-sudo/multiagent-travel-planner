import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import llm_tool

class LLMTests(unittest.TestCase):
    def test_full_message_preserves_tool_calls(self):
        message = SimpleNamespace(content=None, tool_calls=["tool"])
        client = Mock()
        client.chat.completions.create.return_value.choices = [SimpleNamespace(message=message)]
        with patch.object(llm_tool, "_get_client", return_value=client):
            self.assertIs(llm_tool.call_message([]), message)

    def test_empty_choices_is_explicit_error(self):
        client = Mock()
        client.chat.completions.create.return_value.choices = []
        with patch.object(llm_tool, "_get_client", return_value=client):
            with self.assertRaises(RuntimeError):
                llm_tool.call_message([])

class MemoryTests(unittest.TestCase):
    def test_restart_isolation_and_rollback(self):
        from session_store import SessionStore
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "memory.db"
            store = SessionStore(path)
            with store.edit("a", lambda: {"messages": [], "trip_plan": {}}) as state:
                state["messages"].append({"role": "user", "content": "不辣"})
                state["trip_plan"] = {"place": "西湖"}
            restarted = SessionStore(path)
            self.assertEqual(restarted.load("a")["trip_plan"]["place"], "西湖")
            self.assertIsNone(restarted.load("b"))
            with self.assertRaises(RuntimeError):
                with restarted.edit("a", dict) as state:
                    state["messages"] = []
                    raise RuntimeError("model failed")
            self.assertEqual(len(store.load("a")["messages"]), 1)

class RestaurantTests(unittest.TestCase):
    def message(self, content=None, args=None):
        from openai.types.chat import ChatCompletionMessage
        calls = None if args is None else [{"id": "call1", "type": "function", "function": {
            "name": "search_demo_restaurants", "arguments": json.dumps(args)}}]
        return ChatCompletionMessage(role="assistant", content=content, tool_calls=calls, reasoning_content="reasoning")

    def test_two_turns_preserve_history_and_constraints(self):
        from agents import restaurant_agent
        state = {"messages": []}
        args = {"max_price_mop": 80, "require_non_spicy": True, "max_queue_minutes": 20}
        with patch.object(llm_tool, "call_message", side_effect=[self.message(args=args), self.message("mock A")]):
            first = restaurant_agent.run("Macau budget 80, non spicy, queue 20", state)
        self.assertEqual(len(first["evidence"][0]["eligible"]), 1)
        args["max_price_mop"] = 60
        with patch.object(llm_tool, "call_message", side_effect=[self.message(args=args), self.message("no matching mock restaurant")]) as model:
            second = restaurant_agent.run("budget 60, keep other requirements", state)
        self.assertEqual(second["evidence"][0]["eligible"], [])
        sent = model.call_args_list[0].args[0]
        self.assertTrue(any(m.get("reasoning_content") == "reasoning" for m in sent))
        self.assertTrue(any(m.get("content") == "Macau budget 80, non spicy, queue 20" for m in sent))

    def test_mock_label_not_duplicated(self):
        from agents import restaurant_agent
        label = "【澳門餐廳演示｜模擬資料｜MOP】"
        with patch.object(llm_tool, "call_message", return_value=self.message(label + "\n請提供預算")):
            result = restaurant_agent.run("查澳門模擬餐廳", {})
        self.assertEqual(result["reply"].count(label), 1)

    def test_exhaustion_does_not_save_partial_tools(self):
        from agents import restaurant_agent
        state = {"messages": []}
        before = copy.deepcopy(state)
        args = {"max_price_mop": 80, "require_non_spicy": True, "max_queue_minutes": 20}
        with patch.object(llm_tool, "call_message", return_value=self.message(args=args)):
            with self.assertRaises(RuntimeError):
                restaurant_agent.run("找餐廳", state)
        self.assertEqual(state, before)

class ExceptionTests(unittest.TestCase):
    def test_weather_question_does_not_mutate_trip(self):
        import trip_plan
        from agents import exception_agent
        trip = trip_plan.new_trip_plan("test")
        day = trip_plan.get_or_create_day(trip, "day-1")
        trip_plan.add_stop(day, "n1", "attraction", "西湖", "步行", "09:00", "10:00")
        before = copy.deepcopy(trip)
        output = exception_agent.run({"trip_plan": trip}, "unknown", "西湖明天下雨嗎？")
        self.assertEqual(trip, before)
        self.assertTrue(output["requires_confirmation"])

if __name__ == "__main__":
    unittest.main()
