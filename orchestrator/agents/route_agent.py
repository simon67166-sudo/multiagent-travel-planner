"""
行程/路线 Agent -- 把地点写进 trip_plan 某一天的行程节点（链表结构，见 trip_plan.py）。

模型档位：MODEL_LIGHT——`schedule()` mode="trip" 每天排完之后会调一次 _llm_review_day()
做"是否影响体验"的软性复核（结构化判断，轻量模型），其余排班逻辑（选点/算时间）都是纯
确定性计算，不需要 LLM。

唯一入口 schedule(shared_state, candidates, ...)：给编排 Agent 调用——`candidates` 是
content_agent.run() 产出的候选（带经纬度）。mode="nearby" 是最近邻贪心 + 粗粒度小时预算；
mode="trip"（2026-09-15 起）改成时段骨架排班——按 _SLOT_TEMPLATE（上午/下午/晚上景点 +
午餐/晚餐槽位）往里面填候选，槽位类型/时间窗口天然保证类别配比、限制单段跳跃距离，见
_fill_day_skeleton()。真实调 map_tool.py/nearby_sources.py（高德地图 API）算交通时间，
坐标已知时走 _real_leg() 坐标直查，不重新地理编码。

（2026-09-15 起：老接口 run() 已删除——排时间的触发点从"意图分类猜中了 route"改成"用户
从 attraction_picker 选完候选、点确认"之后，server.py 的 apply_selection() 直接改调
schedule()，run() 那种"单一占位日期顺序追加"彻底没有调用方了）
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

import llm_tool
import map_tool
import nearby_sources
import trip_plan
import weather_tool

_WALK_DRIVE_THRESHOLD_M = 2000  # 两站直线距离超过这个就改算驾车，不然走路太久


# ---------------------------------------------------------------------------
# schedule() -- 2026-09-14 新增：统一排时间接口，真正的多天规划
# ---------------------------------------------------------------------------
_DAY_START_TIME = "09:00"
_DAY_HOURS_BUDGET = 11  # mode="nearby" 用的粗粒度预算（09:00-20:00），mode="trip" 改用下面的时段骨架
_MAX_STOPS_PER_DAY = 7  # mode="trip" 骨架 6 个槽位（含早餐）+ 1 个景点槽位加塞，上限跟着调整

# mode="trip" 的时段骨架（2026-09-15 起，2026-09-16 加早餐槽位）：早餐 + 上午/下午/晚上各一个
# 景点槽位，中间穿插午餐/晚餐，槽位类型天然保证了"每天保证早中晚配额"的类别配比，槽位的时间
# 窗口也比"一天11小时"的粗粒度预算严格得多，能防止贪心排班把隔了老远的候选硬塞进同一天。
# 下午/晚上是可选槽位（排不满候选就空着，不阻塞），早餐/上午/午餐/晚餐是必选锚点（见
# _fill_day_skeleton()/_llm_review_day()）。"pool" 字段直接对应 content_agent.py 打好的
# 三选一类别（景点/早餐/午晚餐），不再是笼统的"attraction/meal"二选一角色。
_SLOT_TEMPLATE = [
    {"slot_id": "breakfast", "pool": "breakfast", "start": "08:00", "end": "09:00", "required": True},
    {"slot_id": "morning", "pool": "attraction", "start": "09:15", "end": "12:00", "required": True},
    {"slot_id": "lunch", "pool": "lunch_dinner", "start": "12:00", "end": "13:30", "required": True},
    {"slot_id": "afternoon", "pool": "attraction", "start": "14:00", "end": "17:30", "required": False},
    {"slot_id": "dinner", "pool": "lunch_dinner", "start": "18:00", "end": "19:30", "required": True},
    {"slot_id": "evening", "pool": "attraction", "start": "19:30", "end": "21:30", "required": False},
]
_INSERT_PROXIMITY_M = 800  # 景点槽位排完主候选后，加塞候选必须在这个距离内（比 _nearby_plan() 的 1500m 搜索半径更严格，避免"顺路"变成"绕路"）
_MIN_LEFTOVER_MINUTES_FOR_INSERT = 45  # 这个槽位至少还剩这么多分钟才考虑加塞
_SLOT_FILL_MAX_ATTEMPTS = 3  # 一个槽位最多试几个候选就放弃，避免候选池很大时挨个试到底


def _distance_m(a: dict, b: dict) -> float:
    """haversine 距离（米）。跟 schedule_widgets.py 的 _haversine_m 是同一个公式，这里数据结构
    不同（candidate dict 不是 (lng,lat) tuple），就近独立实现一份，不为了共用几行数学公式
    建立跨模块依赖。"""
    import math

    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlat = lat2 - lat1
    dlon = math.radians(b["lng"] - a["lng"])
    return 6371000 * 2 * math.asin(min(1, math.sqrt(math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)))


def _candidate_role(candidate: dict) -> str:
    """候选是"meal"（餐饮）还是"attraction"（景点）角色——只给 _stay_minutes() 的停留时长
    估算用，粗粒度二选一（早餐/午晚餐都算"meal"）。社区帖子的 category="饮食" 含"食"字；
    高德 POI 的 category 是原始 type 字符串，餐饮大类叫"餐饮服务"，不含"食"字但含"餐"字
    ——两个字都查，才能同时覆盖两种候选来源。查不到 category 就默认"attraction"，安全
    默认值，不会让候选凭空消失。分槽位池子（早餐/午晚餐/景点三选一）用 _pool_for_candidate()，
    不是这个函数。"""
    category = candidate.get("category") or ""
    return "meal" if ("食" in category or "餐" in category) else "attraction"


def _pool_for_candidate(candidate: dict) -> str:
    """候选属于哪个排班池：优先看 category 是不是精确的"景点"/"早餐"/"午晚餐"
    （content_agent.py 的分类质检负责打好这三个标签，正常流程下所有候选都会有）；万一有
    候选没被分类过（防御性兜底，不该发生），退回 _candidate_role() 的粗粒度"食"/"餐"字符
    判断，食物类默认归"lunch_dinner"（更常见的用餐场景），不归"breakfast"（更少见，不该是
    猜测的默认值）。"""
    mapping = {"景点": "attraction", "早餐": "breakfast", "午晚餐": "lunch_dinner"}
    if candidate.get("category") in mapping:
        return mapping[candidate["category"]]
    return "lunch_dinner" if _candidate_role(candidate) == "meal" else "attraction"


def _stay_minutes(candidate: dict) -> int:
    """粗略估算停留时长：餐饮角色按 45 分钟算，景点角色按 30 分钟算——跟原 nearby_planner
    的经验值一致。"""
    return 45 if _candidate_role(candidate) == "meal" else 30


def _real_leg(from_point: dict, to_point: dict, city: str | None) -> dict | None:
    """查 from_point -> to_point 的真实交通方式/耗时（2km 阈值切驾车）。查不到返回 None，
    调用方自己决定怎么降级，不在这里格式化成文字——返回真实分钟数给 _fill_day_skeleton()/
    _place_day() 去推进时间游标，不是拼好的一句人话。

    from_point/to_point 只要带 lng/lat（schedule() 传进来的候选/起点都带，因为
    content_agent.py 已经用 nearby_sources.find_origin()/nearby() 查过一次真实坐标了），
    就直接用 nearby_sources.route() 按坐标查，不再重新地理编码——map_tool.route_between()
    每次都要用地名重新查一次坐标，对港澳这种同名地点多的场景不可靠（这个 session 前面已经
    在 content_agent.py/route_agent.py 的种子扩展里踩过并绕开这个坑，这里用坐标直接查是
    同一个道理，顺便也省一次地理编码的网络往返）。坐标缺失才退回按名字查，理论上不该发生
    （schedule() 调用前已经用 candidate.get("lng") is not None 过滤过），只是防御性兜底。"""
    if from_point.get("lng") is not None and to_point.get("lng") is not None:
        a = {"lng": from_point["lng"], "lat": from_point["lat"], "name": from_point.get("place") or from_point.get("name"), "crs": "GCJ02"}
        b = {"lng": to_point["lng"], "lat": to_point["lat"], "name": to_point.get("place") or to_point.get("name"), "crs": "GCJ02"}
        info = nearby_sources.route(a, b, mode="walking")
        if info["available"] and info["distance_m"] > _WALK_DRIVE_THRESHOLD_M:
            info = nearby_sources.route(a, b, mode="driving")
        if not info["available"]:
            return None
        return {"distance_m": info["distance_m"], "duration_min": info["duration_min"], "mode": info["mode"]}

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
        role = _candidate_role(stop)
        trip_plan.add_stop(
            day_plan, node_id, role, stop.get("place") or stop.get("name"),
            arrival_transport=transport_label, arrival_time=arrival.strftime("%H:%M"),
            end_time=finish.strftime("%H:%M"), after_id=prev_id,
            # 把候选已经查到的真实坐标顺手存进节点里——content_agent.py 老早就查过了，不存
            # 下来的话 schedule_widgets.py 画地图时只能拿地点名字重新地理编码，不可靠也多余
            lng=stop.get("lng"), lat=stop.get("lat"),
            **({"meal_type": "餐饮"} if role == "meal" else {}),
        )
        prev_id = node_id
        cursor = finish
        current_point = stop
    return unscheduled, current_point


def _fill_day_skeleton(
    attraction_pool: list[dict], breakfast_pool: list[dict], lunch_dinner_pool: list[dict], city: str | None
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """mode="trip" 专用：按 _SLOT_TEMPLATE 顺序把候选池里的候选排进当天的时段骨架，先在内存
    里排好（还没调 trip_plan.add_stop()，等 _llm_review_day() 复核完再真正落地）。

    返回 (这一天预排好的有序节点列表, 用剩的 attraction_pool, 用剩的 breakfast_pool, 用剩的
    lunch_dinner_pool)——三个池子要把这天用掉的候选摘除，好让下一天继续从剩下的候选里选，
    跟 _greedy_order() 返回 leftover 的思路一致。

    每个预排节点在原候选字段基础上多带：arrival_time/end_time（datetime）、arrival_transport
    （格式化字符串）、slot_id、anchor（True=早餐/上午主景点/午餐/晚餐，_llm_review_day() 不能
    拿掉）。

    槽位之间的时间是硬楼层：cursor = max(上一段结束时间, 这个槽位的 start)，不会因为上一段
    提前结束就提前开始下一段——刻意模拟真实旅游的节奏感（吃完午饭到下午景点开门之间留出
    休息/自由活动时间），不是 bug。
    """
    attraction_pool = list(attraction_pool)
    breakfast_pool = list(breakfast_pool)
    lunch_dinner_pool = list(lunch_dinner_pool)
    pools = {"attraction": attraction_pool, "breakfast": breakfast_pool, "lunch_dinner": lunch_dinner_pool}
    day_stops: list[dict] = []
    current_point: dict | None = None
    cursor = datetime.strptime(_SLOT_TEMPLATE[0]["start"], "%H:%M")
    stops_today = 0

    def pick_one(pool: list[dict], slot_end: datetime, ordered_candidates: list[dict]) -> dict | None:
        """按 ordered_candidates 给的顺序试最多 _SLOT_FILL_MAX_ATTEMPTS 个，第一个能在
        slot_end 前完成（真实交通+停留时长）的就选中、从 pool 摘除、推进 cursor/current_point，
        返回预排字段；都不行返回 None，pool 不变。"""
        nonlocal current_point, cursor, stops_today
        for candidate in ordered_candidates[:_SLOT_FILL_MAX_ATTEMPTS]:
            if current_point is None:
                arrival, transport_label = cursor, "首站"
            else:
                leg = _real_leg(current_point, candidate, city)
                if leg and leg.get("duration_min") is not None:
                    arrival = cursor + timedelta(minutes=leg["duration_min"])
                    mode_label = "步行" if leg["mode"] == "walking" else "驾车/打车"
                    transport_label = f"{mode_label}约 {leg['duration_min']} 分钟（约 {leg['distance_m'] / 1000:.1f} 公里）"
                else:
                    arrival, transport_label = cursor, "待定（地图查询失败）"
            finish = arrival + timedelta(minutes=_stay_minutes(candidate))
            if finish > slot_end:
                continue
            pool.remove(candidate)
            current_point = candidate
            cursor = finish
            stops_today += 1
            return {**candidate, "arrival_time": arrival, "end_time": finish, "arrival_transport": transport_label}
        return None

    for slot in _SLOT_TEMPLATE:
        if stops_today >= _MAX_STOPS_PER_DAY:
            break
        slot_start = datetime.strptime(slot["start"], "%H:%M")
        slot_end = datetime.strptime(slot["end"], "%H:%M")
        cursor = max(cursor, slot_start)
        pool = pools[slot["pool"]]
        ordered = sorted(pool, key=lambda c: _distance_m(current_point, c)) if current_point else list(pool)
        picked = pick_one(pool, slot_end, ordered)
        if picked is None:
            continue
        day_stops.append({**picked, "slot_id": slot["slot_id"], "anchor": slot["required"]})

        # 景点槽位排完主候选后，槽位还剩足够时间、附近又有很近的同角色候选，就加塞一个——
        # 加塞的不算锚点，_llm_review_day() 觉得不合理可以拿掉
        if slot["pool"] == "attraction" and stops_today < _MAX_STOPS_PER_DAY:
            leftover_minutes = (slot_end - cursor).total_seconds() / 60
            if leftover_minutes >= _MIN_LEFTOVER_MINUTES_FOR_INSERT:
                nearby = sorted(
                    (c for c in attraction_pool if _distance_m(current_point, c) <= _INSERT_PROXIMITY_M),
                    key=lambda c: _distance_m(current_point, c),
                )
                if nearby:
                    inserted = pick_one(attraction_pool, slot_end, nearby)
                    if inserted is not None:
                        day_stops.append({**inserted, "slot_id": slot["slot_id"], "anchor": False})

    return day_stops, attraction_pool, breakfast_pool, lunch_dinner_pool


_DAY_REVIEW_PROMPT = """你是旅游行程质检员。下面是已经排好的一天行程（JSON 数组，每项一站：
index/place/category/slot_id/arrival_time/distance_from_prev_m/recommendation_score），
这份名单里只包含允许被拿掉的站（早餐/上午主景点/午餐/晚餐是必选锚点，已经从名单里排除，
不会出现，你没有机会也不需要考虑它们）。找出明显不合理的站（比如同一天已经有类似口味的
餐饮扎堆、某一站离上一站明显是绕路凑数、体验上不划算），返回要拿掉的 index 列表；
recommendation_score 越高代表这个候选越值得保留，条件差不多时优先拿掉分数低的。没有问题
就返回空数组。只输出 JSON 数组，不要输出其他任何文字。"""


def _llm_review_day(day_stops: list[dict]) -> set[int]:
    """一天排完之后调一次（不是每加一站调一次，控制延迟——这个 demo 光是真实 API 串行调用
    就已经要几分钟），只能拿掉 anchor=False 的站（加塞候选/可选槽位的候选），早餐/上午
    主景点/午餐/晚餐四个必选锚点不在候选范围内，哪怕模型觉得不合理也不会被拿掉——这是
    结构性保证的保底，宁可效果不完美也不能让一次模型判断把"每天保证早中晚配额"这个保证
    打破。解析失败/调用异常一律返回空集合（不拿掉任何东西），绝不能让复核失败拖垮整个排班。"""
    removable = [i for i, s in enumerate(day_stops) if not s.get("anchor")]
    if not removable:
        return set()

    def fmt_time(value):
        return value.strftime("%H:%M") if hasattr(value, "strftime") else value

    payload = [
        {
            "index": i,
            "place": day_stops[i].get("place"),
            "category": day_stops[i].get("category"),
            "slot_id": day_stops[i].get("slot_id"),
            "arrival_time": fmt_time(day_stops[i].get("arrival_time")),
            "distance_from_prev_m": round(_distance_m(day_stops[i - 1], day_stops[i])) if i > 0 else 0,
            "recommendation_score": day_stops[i].get("recommendation_score"),
        }
        for i in removable
    ]
    try:
        raw = llm_tool.call_llm(
            [
                {"role": "system", "content": _DAY_REVIEW_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            model=llm_tool.MODEL_LIGHT,
        )
        clean = raw.strip()
        if clean.startswith("```"):
            clean = "\n".join(clean.splitlines()[1:-1])
        result = json.loads(clean)
        if not isinstance(result, list):
            return set()
        return {i for i in result if isinstance(i, int) and i in removable}
    except Exception:
        return set()


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

    mode="nearby"：单日短途排班，还是用最近邻贪心 + 粗粒度小时预算（_greedy_order()/
      _place_day()）。origin 是真实起点坐标（content_agent.run() 的 nearby_params.origin），
      hours 是游览时长（默认 3）。origin 缺失直接原样退回未排班。
    mode="trip"（默认，2026-09-15 起改成时段骨架排班，2026-09-16 加早餐槽位）：候选按
      _pool_for_candidate() 分成景点/早餐/午晚餐三个池子，每天按 _SLOT_TEMPLATE（早餐 +
      上午/下午/晚上景点 + 午餐/晚餐）从对应池子挑最近的候选填槽位（见 _fill_day_skeleton()），
      排完一天调一次 _llm_review_day() 做"是否影响体验"的软性复核（只能拿掉加塞/可选槽位的
      候选，早餐/上午主景点/午餐/晚餐四个必选锚点不会被拿掉）。没有"起点地标"这个概念，
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
        # 时段骨架排班（2026-09-15 起，2026-09-16 加早餐槽位）：早餐 + 上午/下午/晚上景点
        # 槽位 + 午餐/晚餐槽位，见 _SLOT_TEMPLATE。景点/早餐/午晚餐候选池三分，槽位类型
        # 天然保证类别配比，槽位时间窗口天然限制单段能跳多远——不再是"一天11小时预算"
        # 这种粗粒度贪心。
        attraction_pool = [c for c in geocoded if _pool_for_candidate(c) == "attraction"]
        breakfast_pool = [c for c in geocoded if _pool_for_candidate(c) == "breakfast"]
        lunch_dinner_pool = [c for c in geocoded if _pool_for_candidate(c) == "lunch_dinner"]
        for day_offset in range(time_budget_days):
            if not attraction_pool and not breakfast_pool and not lunch_dinner_pool:
                break
            day_stops, attraction_pool, breakfast_pool, lunch_dinner_pool = _fill_day_skeleton(
                attraction_pool, breakfast_pool, lunch_dinner_pool, city
            )
            if not day_stops:
                continue
            drop = _llm_review_day(day_stops)
            day_key = f"day-{next_day_num + day_offset}"
            day_plan = trip_plan.get_or_create_day(trip, day_key)
            prev_id = None
            for i, stop in enumerate(day_stops):
                if i in drop:
                    unscheduled.append(stop)
                    continue
                node_id = f"{day_plan['date']}-node-{len(day_plan['nodes']) + 1}"
                slot_id = stop.get("slot_id")
                meal_type = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}.get(slot_id)
                trip_plan.add_stop(
                    day_plan, node_id, "meal" if meal_type else "attraction", stop.get("place") or stop.get("name"),
                    arrival_transport=stop["arrival_transport"],
                    arrival_time=stop["arrival_time"].strftime("%H:%M"),
                    end_time=stop["end_time"].strftime("%H:%M"),
                    after_id=prev_id, lng=stop.get("lng"), lat=stop.get("lat"),
                    **({"meal_type": meal_type} if meal_type else {}),
                )
                prev_id = node_id
                last_point = stop
            result_days[day_key] = trip_plan.day_stops(day_plan)
        unscheduled.extend(attraction_pool)
        unscheduled.extend(breakfast_pool)
        unscheduled.extend(lunch_dinner_pool)

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

    print("--- schedule() mode=\"trip\" 自测（时段骨架排班，真实数据）---")
    _trip_shared_state = {
        "persona": _demo_persona, "scenario": "vacation", "city": "澳门",
        "trip_plan": trip_plan.new_trip_plan("route-schedule-trip-demo-trip"),
    }
    _trip_content_result = content_agent.run(_trip_shared_state, location_hint="推荐一个3天的行程", mode="trip")
    _trip_schedule_result = schedule(
        _trip_shared_state, _trip_content_result["recommendations"], city="澳门", mode="trip", time_budget_days=3,
    )
    print(json.dumps(_trip_schedule_result, ensure_ascii=False, indent=2))
