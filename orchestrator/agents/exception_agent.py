"""
异常应变 Agent -- 提案制：两条独立路径都只评估、不执行，返回结果里 requires_confirmation
恒为 True，谁都不直接碰 trip_plan。真正"用户确认后执行调整"的接口还没做（见
docs/orchestrator-guide.md"待接事项"），编排 Agent 目前只负责把提案说给用户听。

1. run()：按地点名字匹配 trip_plan 里的行程节点（纯子串匹配，event_detail 是用户整句话，
   event_verified 恒为 False——纯粹是"消息里提到了这个地名"，没有验证过是不是真的发生了）
2. check_weather()：真查 weather_tool.py（和风天气灾害预警），event_verified 恒为 True——
   跟 run() 的关键区别是这条数据来自真实 API，不是靠子串猜的，但一样不自动改行程

模型档位：架构文档里说是"中等模型"，目前只有 llm_tool.MODEL_FULL/MODEL_LIGHT 两档，先待定。
"""

import sys
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

import weather_tool

# 和风天气 severity 取值的严重程度排序，数字越大越严重
_SEVERITY_ORDER = {"unknown": 0, "minor": 1, "moderate": 2, "severe": 3, "extreme": 4}
# 预警等级达到这个，needs_replan 才标 True（提示编排 Agent 用更急迫的语气告诉用户），
# 但不管等级多高都不会自动改 trip_plan——这只是"建不建议优先处理"的信号，不是执行开关
_NEEDS_REPLAN_SEVERITY = "severe"


def run(shared_state: dict, event_type: str, event_detail: str | None = None) -> dict:
    """
    纯子串匹配：整句用户消息去匹配行程里的地点名字，命中就列进 affected_locations，
    很粗糙，真实版本应该先做实体识别。不管命不命中都不碰 trip_plan，只返回评估结果。
    """
    affected = []
    for day in shared_state["trip_plan"]["days"].values():
        for node in day["nodes"].values():
            if node.get("place") and node["place"] in (event_detail or ""):
                affected.append(node["place"])
    return {
        "needs_replan": bool(affected) and event_type != "unknown",
        "requires_confirmation": True,
        "event_verified": False,
        "affected_locations": affected,
        "suggested_adjustment": "行程尚未变更。请先确认事件信息，再决定是否调整受影响地点。",
        "event_detail": event_detail,
    }


def check_weather(shared_state: dict, city: str) -> dict:
    """
    查 city 当前是否有真实生效的灾害预警。有的话返回一份未验证提案（event_verified=True，
    因为数据本身是真的，但"要不要调整行程"仍然没有执行，交给编排 Agent 汇总后问用户）。

    没有预警返回 has_warning=False，是正常情况。查询本身失败（key 没配/地名查不到/网络问题）
    不抛异常往上炸，优雅降级返回带 error 字段的结果——跟 route_agent.py 里 map_tool 查询失败
    的处理方式是同一个思路。
    """
    try:
        warnings = weather_tool.get_active_warnings(city)
    except Exception as e:
        return {
            "has_warning": False,
            "warnings": [],
            "requires_confirmation": False,
            "needs_replan": False,
            "error": str(e),
        }

    if not warnings:
        return {"has_warning": False, "warnings": [], "requires_confirmation": False, "needs_replan": False}

    worst_severity = max((w["severity"] for w in warnings), key=lambda s: _SEVERITY_ORDER.get(s, 0))
    return {
        "has_warning": True,
        "warnings": warnings,
        "event_verified": True,
        "requires_confirmation": True,
        "needs_replan": _SEVERITY_ORDER.get(worst_severity, 0) >= _SEVERITY_ORDER[_NEEDS_REPLAN_SEVERITY],
        "suggested_adjustment": f"{city} 当前有真实灾害预警（最高等级：{worst_severity}）。行程尚未变更，是否需要调整由你决定。",
    }


if __name__ == "__main__":
    import json

    import trip_plan

    demo_trip = trip_plan.new_trip_plan("exception-agent-demo-trip")
    demo_day = trip_plan.get_or_create_day(demo_trip, "day-1")
    trip_plan.add_stop(demo_day, "n1", "attraction", "西湖", "地铁", "09:00", "11:00")
    demo_shared_state = {"trip_plan": demo_trip}

    result = run(demo_shared_state, event_type="closure", event_detail="西湖今天临时封闭了")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    print("--- check_weather 自测（真实调用和风天气 API，用一个当前有真实预警的城市）---")
    weather_result = check_weather(demo_shared_state, "海口")
    print(json.dumps(weather_result, ensure_ascii=False, indent=2))
