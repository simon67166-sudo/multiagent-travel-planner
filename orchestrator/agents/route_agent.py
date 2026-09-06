"""
行程/路线 Agent -- 把地点写进 trip_plan 某一天的行程节点（链表结构，见 trip_plan.py）。

模型档位：MODEL_LIGHT（路线算法包装，轻量模型）—— 目前还没接 LLM，是纯结构化写入；
真正接路线规划算法/地图 API 时，需要用 LLM 包装自然语言结果的话在这个文件里接
llm_tool.call_llm(messages, model=llm_tool.MODEL_LIGHT)。
"""

import sys
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

import trip_plan

_PLACEHOLDER_DAY = "day-1"  # 占位：还没做真正的多日期规划，先都写进同一天


def run(shared_state: dict, places: list[str] | None, time_budget: str | None = None) -> dict:
    """
    "怎么排序/怎么估算交通方式和时间"这些还是占位（时间字段先填占位文字），
    真正的路线规划算法接管后，写入的还是同一个 trip_plan 结构，返回字段不用变。
    """
    day_plan = trip_plan.get_or_create_day(shared_state["trip_plan"], _PLACEHOLDER_DAY)

    prev_id = day_plan["head_id"]
    while prev_id and day_plan["nodes"][prev_id]["next_id"]:
        prev_id = day_plan["nodes"][prev_id]["next_id"]

    for place in places or ["占位地点 A"]:
        node_id = f"{_PLACEHOLDER_DAY}-node-{len(day_plan['nodes']) + 1}"
        trip_plan.add_stop(
            day_plan,
            node_id,
            "attraction",
            place,
            arrival_transport="待定",
            arrival_time="待定",
            end_time="待定",
            after_id=prev_id,
        )
        prev_id = node_id

    return {"route": trip_plan.day_stops(day_plan)}


if __name__ == "__main__":
    import json

    demo_shared_state = {"trip_plan": trip_plan.new_trip_plan("route-agent-demo-trip")}
    print(json.dumps(run(demo_shared_state, places=["西湖", "灵隐寺"]), ensure_ascii=False, indent=2))
