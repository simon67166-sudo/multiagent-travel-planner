"""
行程/路线 Agent -- 把地点写进 trip_plan 某一天的行程节点（链表结构，见 trip_plan.py）。

模型档位：MODEL_LIGHT（路线算法包装，轻量模型）—— 目前还没接 LLM，是纯结构化写入；
真正需要用 LLM 包装自然语言结果时在这个文件里接 llm_tool.call_llm(messages, model=llm_tool.MODEL_LIGHT)。

两个入口，都会真实调 map_tool.py（高德地图 API）算交通时间：
- run(shared_state, places, ...)：旧接口，`places` 是一串地点名字字符串，全部按顺序写进
  同一个占位日期。server.py 的 `POST /widget-response`（attraction_picker 确认）还在用这个，
  保留不动。
- schedule(shared_state, candidates, ...)：2026-09-14 新增的统一排时间接口，给编排 Agent
  调用——`candidates` 是 content_agent.run() 产出的候选（带经纬度），贪心排出真正的多天
  行程结构（不再是单一占位日期），到达/结束时间是真算出来的，不再是"待定"占位文字。
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

import map_tool
import trip_plan
import weather_tool

_PLACEHOLDER_DAY = "day-1"  # run() 专用占位：还没做多日期规划的老接口，继续写进同一天
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


# ---------------------------------------------------------------------------
# schedule() -- 2026-09-14 新增：统一排时间接口，真正的多天规划
# ---------------------------------------------------------------------------
_DAY_START_TIME = "09:00"
_DAY_HOURS_BUDGET = 11  # mode="trip" 每天默认排班时长（09:00-20:00）
_MAX_STOPS_PER_DAY = 6  # 跟原 nearby_planner 的上限一致


def _distance_m(a: dict, b: dict) -> float:
    """haversine 距离（米）。跟 schedule_widgets.py 的 _haversine_m 是同一个公式，这里数据结构
    不同（candidate dict 不是 (lng,lat) tuple），就近独立实现一份，不为了共用几行数学公式
    建立跨模块依赖。"""
    import math

    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlat = lat2 - lat1
    dlon = math.radians(b["lng"] - a["lng"])
    return 6371000 * 2 * math.asin(min(1, math.sqrt(math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)))


def _stay_minutes(candidate: dict) -> int:
    """粗略估算停留时长：类别里带"食"字（社区帖子的"饮食"分类、高德"餐饮服务;..."分类都命中）
    按 45 分钟算，其余（景点类）按 30 分钟算——跟原 nearby_planner 的经验值一致。"""
    return 45 if "食" in (candidate.get("category") or "") else 30


def _real_leg(from_point: dict, to_point: dict, city: str | None) -> dict | None:
    """查 from_point -> to_point 的真实交通方式/耗时（2km 阈值切驾车，跟 _estimate_transport
    同一个阈值）。查不到返回 None，调用方自己决定怎么降级，不在这里格式化成文字
    （_estimate_transport 是给 run() 用的，返回值直接是格式化字符串；这个是给 schedule()
    用的，需要拿到真实分钟数去推进时间游标，不能只有一句人话）。"""
    from_name = from_point.get("place") or from_point.get("name")
    to_name = to_point.get("place") or to_point.get("name")
    try:
        info = map_tool.route_between(from_name, to_name, mode="walking", city=city)
        if info["distance_m"] > _WALK_DRIVE_THRESHOLD_M:
            info = map_tool.route_between(from_name, to_name, mode="driving", city=city)
        return info
    except Exception:
        return None


def _greedy_order(pool: list[dict], start_point: dict, max_stops: int) -> tuple[list[dict], list[dict]]:
    """从 start_point 出发，贪心每次选剩余候选里离当前位置最近的一个，直到选够 max_stops 个
    或候选耗尽。返回 (选中的有序列表, 没选进来剩下的候选)。"""
    pool = list(pool)
    current = start_point
    selected: list[dict] = []
    while pool and len(selected) < max_stops:
        nxt = min(pool, key=lambda c: _distance_m(current, c))
        pool.remove(nxt)
        selected.append(nxt)
        current = nxt
    return selected, pool


def _place_day(day_plan: dict, ordered_stops: list[dict], start_point: dict | None, start_time_str: str, hour_budget: float, city: str | None) -> tuple[list[dict], dict | None]:
    """把 ordered_stops 按顺序真实排进 day_plan（真查交通时间、真推进时间游标），超出
    hour_budget 的切进 unscheduled 返回，不硬塞。start_point=None 表示这一天没有真实起点
    （mode="trip" 场景，没有"从哪出发"这个概念），第一站标"首站"；start_point 给了真实坐标
    （mode="nearby" 场景，起点是用户给的真实地标）就连第一站也查真实交通时间。
    返回 (未排进去的候选, 这一天最后一个真实排进去的点——给外层查天气用)。"""
    cursor = datetime.strptime(start_time_str, "%H:%M")
    deadline = cursor + timedelta(hours=hour_budget)
    current_point = start_point
    prev_id = None
    unscheduled: list[dict] = []
    for stop in ordered_stops:
        if current_point is None:
            arrival, transport_label = cursor, "首站"
        else:
            leg = _real_leg(current_point, stop, city)
            if leg and leg.get("duration_min") is not None:
                arrival = cursor + timedelta(minutes=leg["duration_min"])
                mode_label = "步行" if leg["mode"] == "walking" else "驾车/打车"
                transport_label = f"{mode_label}约 {leg['duration_min']} 分钟（约 {leg['distance_m'] / 1000:.1f} 公里）"
            else:
                arrival, transport_label = cursor, "待定（地图查询失败）"
        stay = _stay_minutes(stop)
        finish = arrival + timedelta(minutes=stay)
        if finish > deadline:
            unscheduled.append(stop)
            continue
        node_id = f"{day_plan['date']}-node-{len(day_plan['nodes']) + 1}"
        trip_plan.add_stop(
            day_plan, node_id, "attraction", stop.get("place") or stop.get("name"),
            arrival_transport=transport_label, arrival_time=arrival.strftime("%H:%M"),
            end_time=finish.strftime("%H:%M"), after_id=prev_id,
        )
        prev_id = node_id
        cursor = finish
        current_point = stop
    return unscheduled, current_point


def schedule(
    shared_state: dict,
    candidates: list[dict],
    city: str | None,
    mode: str = "trip",
    time_budget_days: int = 1,
    hours: float | None = None,
    origin: dict | None = None,
) -> dict:
    """
    统一排时间接口，给编排 Agent 调用：拿达人 Agent（content_agent.run()）给的候选列表，
    贪心排出具体到达/结束时间，真写进 trip_plan——真正的多天结构（新开 day-N），不再是
    run() 那种单一占位日期。

    mode="nearby"：单日短途排班。origin 是真实起点坐标（content_agent.run() 的
      nearby_params.origin），hours 是游览时长（默认 3）。origin 缺失直接原样退回未排班。
    mode="trip"（默认）：按 time_budget_days 天数贪心分配候选到各天，每天默认排到
      _DAY_HOURS_BUDGET 小时、最多 _MAX_STOPS_PER_DAY 站；没有"起点地标"这个概念，
      每天第一站标"首站"。

    candidates 里没有 lng/lat 的（比如口碑复核阶段没查到坐标的候选）直接进 unscheduled，
    不硬凑坐标。天气只查最后排到的那个点附近未来 24 小时（避免每天/每站都查一次天气
    浪费调用次数），调 weather_tool.get_hourly_forecast()。

    返回 {"days": {day_key: [...该天已排好的节点，day_stops() 格式]},
      "weather_reminders": [...], "unscheduled": [...没能排进任何一天的候选]}
    """
    geocoded = [c for c in candidates if c.get("lng") is not None and c.get("lat") is not None]
    unscheduled: list[dict] = [c for c in candidates if c not in geocoded]

    trip = shared_state["trip_plan"]
    next_day_num = 1
    while f"day-{next_day_num}" in trip["days"]:
        next_day_num += 1

    result_days: dict[str, list[dict]] = {}
    last_point: dict | None = None

    if mode == "nearby":
        if origin is None:
            return {"days": {}, "weather_reminders": [], "unscheduled": candidates}
        ordered, leftover = _greedy_order(geocoded, origin, _MAX_STOPS_PER_DAY)
        day_key = f"day-{next_day_num}"
        day_plan = trip_plan.get_or_create_day(trip, day_key)
        day_unscheduled, last_point = _place_day(day_plan, ordered, origin, _DAY_START_TIME, hours or 3, city)
        result_days[day_key] = trip_plan.day_stops(day_plan)
        unscheduled.extend(leftover)
        unscheduled.extend(day_unscheduled)
    else:
        pool = geocoded
        for day_offset in range(time_budget_days):
            if not pool:
                break
            ordered, pool = _greedy_order(pool, pool[0], _MAX_STOPS_PER_DAY)
            day_key = f"day-{next_day_num + day_offset}"
            day_plan = trip_plan.get_or_create_day(trip, day_key)
            day_unscheduled, last_point = _place_day(day_plan, ordered, None, _DAY_START_TIME, _DAY_HOURS_BUDGET, city)
            result_days[day_key] = trip_plan.day_stops(day_plan)
            unscheduled.extend(day_unscheduled)
        unscheduled.extend(pool)

    weather_reminders: list[str] = []
    if last_point is not None:
        try:
            forecast = weather_tool.get_hourly_forecast(last_point["lng"], last_point["lat"], hours=24)
            rain = [h["rain_probability"] for h in forecast if h.get("rain_probability") is not None]
            if rain and max(rain) >= 0.6:
                weather_reminders.append("接下来 24 小时降雨概率较高，建议带雨具，行程尚未自动更改。")
        except Exception:
            pass

    return {"days": result_days, "weather_reminders": weather_reminders, "unscheduled": unscheduled}


if __name__ == "__main__":
    import json

    demo_shared_state = {"trip_plan": trip_plan.new_trip_plan("route-agent-demo-trip")}
    print(json.dumps(run(demo_shared_state, places=["杭州西湖", "杭州灵隐寺"], city="杭州"), ensure_ascii=False, indent=2))

    print("--- schedule() 自测（真实数据，走 content_agent 产出的候选）---")
    import sys as _sys
    _sys.path.insert(0, str(_ORCHESTRATOR_DIR))
    from agents import content_agent
    import persona as _persona

    _demo_persona = _persona.bootstrap_from_onboarding(
        "route-schedule-demo-user", "vacation",
        {"pace_score": 0.2, "budget_score": 0.5, "social_mode": "family",
         "interest_theme": ["自然风光"], "taste": ["江浙菜"], "novelty_score": 0.3},
    )
    _demo_shared_state = {
        "persona": _demo_persona, "scenario": "vacation", "city": "澳门",
        "trip_plan": trip_plan.new_trip_plan("route-schedule-demo-trip"),
    }
    _content_result = content_agent.run(_demo_shared_state, location_hint="从大三巴出发逛3小时", mode="nearby")
    _schedule_result = schedule(
        _demo_shared_state, _content_result["recommendations"], city="澳门", mode="nearby",
        hours=_content_result["nearby_params"]["hours"], origin=_content_result["nearby_params"]["origin"],
    )
    print(json.dumps(_schedule_result, ensure_ascii=False, indent=2))
