import copy
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

class ExceptionTests(unittest.TestCase):
    def test_weather_question_does_not_mutate_trip(self):
        import trip_plan
        from agents import exception_agent
        trip = trip_plan.new_trip_plan("test")
        day = trip_plan.get_or_create_day(trip, "day-1")
        trip_plan.add_stop(day, "n1", "attraction", "西湖", "步行", "09:00", "10:00")
        before = copy.deepcopy(trip)
        output = exception_agent.run({"trip_plan": trip}, "unknown", "西湖明天下雨吗？")
        self.assertEqual(trip, before)
        self.assertTrue(output["requires_confirmation"])

if __name__ == "__main__":
    unittest.main()
