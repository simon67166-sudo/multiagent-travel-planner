"""
异常应变 Agent -- 按地点名字匹配 trip_plan 里的行程节点，命中就删掉并记一条天气异常。

模型档位：架构文档里说是"中等模型"，目前只有 llm_tool.MODEL_FULL/MODEL_LIGHT 两档，先待定。

现状："怎么判断某条消息对应哪个地点/要不要真的删"这类判断逻辑很粗糙（纯子串匹配），
真实的异常应变 Agent 接管后可以换成更靠谱的实现（比如先做实体识别抽出具体地点），
返回字段不用变。
"""

import sys
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

import trip_plan


def run(shared_state: dict, event_type: str, event_detail: str | None = None) -> dict:
    trip = shared_state["trip_plan"]
    affected: list[str] = []

    if event_detail:
        for day_plan in trip["days"].values():
            for node_id, node in list(day_plan["nodes"].items()):
                if node["place"] in event_detail:
                    trip_plan.remove_stop(day_plan, node_id)
                    affected.append(node["place"])

    trip_plan.add_weather_alert(
        trip,
        {
            "location": event_detail,
            "event_type": event_type,
            "severity": "未知",
            "description": f"触发异常应变：{event_type}",
            "affects_dates": list(trip["days"].keys()),
        },
    )

    return {
        "needs_replan": bool(affected),
        "suggested_adjustment": f"已从行程中移除受影响地点：{affected}" if affected else "未在当前行程中找到受影响地点，暂不需要调整",
        "affected_locations": affected,
    }


if __name__ == "__main__":
    import json

    demo_trip = trip_plan.new_trip_plan("exception-agent-demo-trip")
    demo_day = trip_plan.get_or_create_day(demo_trip, "day-1")
    trip_plan.add_stop(demo_day, "n1", "attraction", "西湖", "地铁", "09:00", "11:00")
    demo_shared_state = {"trip_plan": demo_trip}

    result = run(demo_shared_state, event_type="closure", event_detail="西湖今天临时封闭了")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(trip_plan.render(demo_trip), ensure_ascii=False, indent=2))
