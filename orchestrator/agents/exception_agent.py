"""
异常应变 Agent -- 提案制：run()/check_weather() 两条评估路径都只评估、不执行，返回结果里
requires_confirmation 恒为 True，谁都不直接碰 trip_plan。apply_adjustment() 才是"用户确认后
真正执行"的那一步（2026-09-14 补上，之前只有评估、没有执行）。

1. run()：按地点名字匹配 trip_plan 里的行程节点（纯子串匹配，event_detail 是用户整句话，
   event_verified 恒为 False——纯粹是"消息里提到了这个地名"，没有验证过是不是真的发生了）
2. check_weather()：真查 weather_tool.py（和风天气灾害预警），event_verified 恒为 True——
   跟 run() 的关键区别是这条数据来自真实 API，不是靠子串猜的，但一样不自动改行程
3. apply_adjustment()：用户明确确认之后，编排 Agent 拿着 affected_locations 里的地点名字
   调这个，才会真的从 trip_plan 里删掉对应节点——什么时候该调这个（怎么判断"用户确认了"）
   还没接进 orchestrate()，见 docs/orchestrator-guide.md"待接事项"，这里只是先把执行接口
   准备好

模型档位：架构文档里说是"中等模型"，目前只有 llm_tool.MODEL_FULL/MODEL_LIGHT 两档，先待定。
"""

import sys
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

import trip_plan
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


def apply_adjustment(shared_state: dict, place: str) -> dict:
    """
    用户确认之后真正执行：把行程里跟 place 同名的节点删掉（可能跨天/命中多个）。
    place 通常就是 run()/check_weather() 提案里 affected_locations 的某一项——
    提案阶段只是"发现了同名节点"，不删；这里才是真删。

    返回 {"cancelled": [被删节点列表，每条带 date/node_id], "count": 数量}；
    count=0 说明没找到匹配的节点（比如提案之后用户自己又改过行程），不算错误，
    调用方（编排 Agent）可以据此告诉用户"没找到这个地点，可能已经不在行程里了"。
    """
    cancelled = trip_plan.cancel_stops_by_place(shared_state["trip_plan"], place)
    return {"cancelled": cancelled, "count": len(cancelled)}


if __name__ == "__main__":
    import json

    demo_trip = trip_plan.new_trip_plan("exception-agent-demo-trip")
    demo_day = trip_plan.get_or_create_day(demo_trip, "day-1")
    trip_plan.add_stop(demo_day, "n1", "attraction", "西湖", "地铁", "09:00", "11:00")
    demo_shared_state = {"trip_plan": demo_trip}

    result = run(demo_shared_state, event_type="closure", event_detail="西湖今天临时封闭了")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    print("--- apply_adjustment 自测：用户确认后真的把西湖删掉 ---")
    apply_result = apply_adjustment(demo_shared_state, "西湖")
    print(json.dumps(apply_result, ensure_ascii=False, indent=2))
    print("确认行程里没有西湖了：", trip_plan.day_stops(demo_day))

    print("--- check_weather 自测（真实调用和风天气 API，用一个当前有真实预警的城市）---")
    weather_result = check_weather(demo_shared_state, "海口")
    print(json.dumps(weather_result, ensure_ascii=False, indent=2))
