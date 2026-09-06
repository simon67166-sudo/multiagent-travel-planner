"""
行程/路线 Agent -- 把地点写进 trip_plan 某一天的行程节点（链表结构，见 trip_plan.py）。

模型档位：MODEL_LIGHT（路线算法包装，轻量模型）—— 目前还没接 LLM，是纯结构化写入；
真正需要用 LLM 包装自然语言结果时在这个文件里接 llm_tool.call_llm(messages, model=llm_tool.MODEL_LIGHT)。

相邻两站之间的交通方式/耗时用 map_tool.py（高德地图 API）真实计算，不再是占位文字；
到达/结束的具体钟点时间（arrival_time/end_time）还是占位——那需要"一天几点开始"+
"每个地方玩多久"这类还没定义的调度逻辑，跟地图 API 是两回事，留着待定。
"""

import sys
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

import map_tool
import trip_plan

_PLACEHOLDER_DAY = "day-1"  # 占位：还没做真正的多日期规划，先都写进同一天
_WALK_DRIVE_THRESHOLD_M = 2000  # 两站直线距离超过这个就改算驾车，不然走路太久


def _estimate_transport(prev_place: str, place: str, city: str | None) -> str:
    """查 map_tool 算 prev_place -> place 的真实交通方式/耗时；查不到就退化成占位文字。"""
    try:
        info = map_tool.route_between(prev_place, place, mode="walking", city=city)
        if info["distance_m"] > _WALK_DRIVE_THRESHOLD_M:
            info = map_tool.route_between(prev_place, place, mode="driving", city=city)
        mode_label = "步行" if info["mode"] == "walking" else "驾车/打车"
        return f"{mode_label}约 {info['duration_min']} 分钟（约 {info['distance_m'] / 1000:.1f} 公里）"
    except Exception as e:
        return f"待定（地图查询失败：{e}）"


def run(
    shared_state: dict, places: list[str] | None, time_budget: str | None = None, city: str | None = None
) -> dict:
    """
    city: 可选的城市提示，传了地理编码更准（比如"西湖"在多个城市都有同名地点）。
    """
    day_plan = trip_plan.get_or_create_day(shared_state["trip_plan"], _PLACEHOLDER_DAY)

    prev_id = day_plan["head_id"]
    while prev_id and day_plan["nodes"][prev_id]["next_id"]:
        prev_id = day_plan["nodes"][prev_id]["next_id"]
    prev_place = day_plan["nodes"][prev_id]["place"] if prev_id else None

    for place in places or ["占位地点 A"]:
        arrival_transport = _estimate_transport(prev_place, place, city) if prev_place else "首站"

        node_id = f"{_PLACEHOLDER_DAY}-node-{len(day_plan['nodes']) + 1}"
        trip_plan.add_stop(
            day_plan,
            node_id,
            "attraction",
            place,
            arrival_transport=arrival_transport,
            arrival_time="待定",
            end_time="待定",
            after_id=prev_id,
        )
        prev_id = node_id
        prev_place = place

    return {"route": trip_plan.day_stops(day_plan)}


if __name__ == "__main__":
    import json

    demo_shared_state = {"trip_plan": trip_plan.new_trip_plan("route-agent-demo-trip")}
    print(json.dumps(run(demo_shared_state, places=["杭州西湖", "杭州灵隐寺"], city="杭州"), ensure_ascii=False, indent=2))
