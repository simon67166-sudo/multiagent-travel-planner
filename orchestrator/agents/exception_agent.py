"""
异常应变 Agent -- 两条独立路径：
1. run()：按地点名字匹配 trip_plan 里的行程节点，命中就删掉并记一条天气异常（纯子串匹配，
   event_type/event_detail 由调用方给，目前是编排 Agent 直接把用户整句话原样传进来）
2. check_weather()：真查 weather_tool.py（和风天气灾害预警），不依赖用户有没有主动提到天气，
   城市级别的真实预警数据自动记录 + 视严重程度决定要不要清空行程

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
import weather_tool

# 和风天气 severity 取值的严重程度排序，数字越大越严重
_SEVERITY_ORDER = {"unknown": 0, "minor": 1, "moderate": 2, "severe": 3, "extreme": 4}
# 预警等级达到这个才自动清空当天行程；城市级预警没法判断"具体哪个景点受影响"，
# 只能整体处理，所以严重程度门槛拉高一点，避免轻微预警也把行程清空
_AUTO_REMOVE_SEVERITY = "severe"


def check_weather(shared_state: dict, city: str) -> dict:
    """
    查 city 当前是否有真实生效的灾害预警，有的话记一条真实字段（而不是 run() 里那种
    "未知"占位）的 weather_alert 进 trip_plan；预警等级达到 severe/extreme 才自动清空
    整个行程（城市级极端天气不像 run() 那样能按地点名字定位到具体节点），moderate/minor
    只记录、不动行程，留给用户自己决定。

    没有预警返回 has_warning=False，是正常情况。查询本身失败（key 没配/地名查不到/网络问题）
    不抛异常往上炸，优雅降级返回 error 字段，不影响 orchestrate() 主流程——跟 route_agent.py
    里 map_tool 查询失败的处理方式是同一个思路。
    """
    try:
        warnings = weather_tool.get_active_warnings(city)
    except Exception as e:
        return {"has_warning": False, "warnings": [], "affected_locations": [], "needs_replan": False, "error": str(e)}

    if not warnings:
        return {"has_warning": False, "warnings": [], "affected_locations": [], "needs_replan": False}

    trip = shared_state["trip_plan"]
    for w in warnings:
        trip_plan.add_weather_alert(
            trip,
            {
                "location": city,
                "event_type": w["event_type"],
                "severity": w["severity"],
                "description": w["headline"] or w["description"],
                "affects_dates": list(trip["days"].keys()),
            },
        )

    worst_severity = max((w["severity"] for w in warnings), key=lambda s: _SEVERITY_ORDER.get(s, 0))
    affected: list[str] = []
    if _SEVERITY_ORDER.get(worst_severity, 0) >= _SEVERITY_ORDER[_AUTO_REMOVE_SEVERITY]:
        for day_plan in trip["days"].values():
            for node_id, node in list(day_plan["nodes"].items()):
                trip_plan.remove_stop(day_plan, node_id)
                affected.append(node["place"])

    return {
        "has_warning": True,
        "warnings": warnings,
        "affected_locations": affected,
        "needs_replan": bool(affected),
    }


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

    print("--- check_weather 自测（真实调用和风天气 API，用一个当前有真实预警的城市）---")
    weather_demo_trip = trip_plan.new_trip_plan("exception-agent-weather-demo")
    weather_demo_day = trip_plan.get_or_create_day(weather_demo_trip, "day-1")
    trip_plan.add_stop(weather_demo_day, "n1", "attraction", "海口骑楼老街", "步行", "09:00", "11:00")
    weather_result = check_weather({"trip_plan": weather_demo_trip}, "海口")
    print(json.dumps(weather_result, ensure_ascii=False, indent=2))
