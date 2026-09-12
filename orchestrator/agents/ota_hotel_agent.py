"""
OTA/酒店 Agent -- 查库存/比价。

模型档位：MODEL_LIGHT（结构化查库存比价，轻量模型）。

现状：**酒店这半边已经接了真实数据**（`hotel_tool.py`，道旅 RollingGo 的真实酒店库存/价格，
2026-09-13 接入），机票这半边还是占位假数据——调研过一圈（携程/去哪儿企业合作制、Kiwi/Amadeus
关闭自助注册、Travelpayouts 要联盟审核+实时数据要求 5 万月活、FlightAPI.io 只有 20 次免费调用），
没有真正免费的个人开发者可用来源，也明确决定不用爬虫方案（绕开正常接口抓商业网站数据，大概率
违反对方服务条款，demo 用途也不例外）。折中方案：`_generate_beijing_flights()` 按真实通航航司/
真实航班号格式/真实大致飞行时长，循环排出北京↔港澳每小时一班的**去程+回程**示例数据（具体班次
是编的，不代表真实存在的某趟航班），价格按城市+航班号+日期做确定性伪随机浮动（同一天同一趟查
多次价格一致，换天会不一样），至少比之前"上海虹桥→杭州萧山"这种跟港澳 demo 完全对不上、价格
写死的老占位数据靠谱一些。等 RollingGo 的机票 MCP 恢复（見文末待接事项）再换成真实数据。

字段补全到"确认预订后能直接落地"需要的程度（机票带 flight_no/from_/to/depart_time/arrive_time，
酒店带 address/check_in/check_out）——这样 server.py 的 POST /widget-response 收到用户选中的候选后，
能直接拿这些字段调 trip_plan.add_flight()/add_hotel()，不用现造字段。date_range 传了就用来填
check_in/check_out，格式 "开始日期~结束日期"。
"""

import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

import hotel_tool
import trip_plan as trip_plan_module

_DEFAULT_STAY_NIGHTS = 2

# 机票候选还是没有真实数据源（见文件顶部说明），但至少让航司/航班号/时长贴近真实航线，不是随手编的
# 上海虹桥→杭州萧山那种跟港澳 demo 完全对不上的老数据。价格/余票/具体班次是演示用途虚构值，不是
# 实时报价——数据来源：2026-09-13 查了北京首都/大兴机场到香港/澳门的真实通航航司（国航/国泰/港航/
# 海航 飞香港，南航/国航/东航 飞澳门）和大致飞行时长，航班号按真实航司二字码编号段编的示例号，
# 循环这几家航司排出每小时一班，不代表某一趟具体真实航班。去程/回程都有：同一批航司往返对飞，
# 回程航班号按真实惯例是去程号 +1（比如去程 CA111，回程 CA112）。
_ROUTE_AIRLINES = {
    "香港": [
        ("中国国际航空", "CA", 111, "北京首都国际机场"),
        ("国泰航空", "CX", 330, "北京首都国际机场"),
        ("香港航空", "HX", 330, "北京大兴国际机场"),
        ("海南航空", "HU", 486, "北京首都国际机场"),
    ],
    "澳门": [
        ("中国南方航空", "CZ", 3505, "北京首都国际机场"),
        ("中国国际航空", "CA", 1305, "北京大兴国际机场"),
        ("中国东方航空", "MU", 2951, "北京首都国际机场"),
    ],
}
_ROUTE_DURATION_MIN = {"香港": 205, "澳门": 210}  # 约3小时25/30分钟，取自公开航线平均飞行时长
_DESTINATION_AIRPORT = {"香港": "香港国际机场", "澳门": "澳门国际机场"}
_FLIGHT_START_HOUR, _FLIGHT_END_HOUR = 7, 21  # 07:00-21:00，每小时一班


def _flight_price(seed_key: str, hour: int) -> int:
    """演示用虚构价格：拿 seed_key（城市+航班号+日期拼出来的字符串）做确定性伪随机浮动——
    同一天同一趟航班查多次价格是同一个数（不会一刷新就变，像真出 bug 一样），换一天/换航班
    就会不一样；叠加早晚高峰固定溢价。不是真实定价逻辑，纯粹让 demo 数据别每次都长一个样。"""
    base = 1280 + random.Random(seed_key).randint(-150, 350)
    return base + (270 if hour in (7, 8, 19, 20) else 0)


def _generate_beijing_flights(city: str, on_date: str, direction: str) -> list[dict]:
    """按 _ROUTE_AIRLINES 循环排班，每小时一班。direction="depart" 是北京→city（去程），
    "return" 是 city→北京（回程）。不是真实时刻表，是贴近真实航线的演示数据。"""
    airlines = _ROUTE_AIRLINES.get(city)
    if not airlines:
        depart = direction == "depart"
        return [{
            "airline": "中国东方航空", "flight_no": "MU5137" if depart else "MU5138",
            "from_": "上海虹桥国际机场" if depart else city, "to": city if depart else "上海虹桥国际机场",
            "depart_time": "08:00", "arrive_time": "10:10", "price": 890,
        }]

    duration = _ROUTE_DURATION_MIN.get(city, 210)
    dest_airport = _DESTINATION_AIRPORT.get(city, city)
    routes = []
    for i, hour in enumerate(range(_FLIGHT_START_HOUR, _FLIGHT_END_HOUR + 1)):
        name, code, base_no, bjs_airport = airlines[i % len(airlines)]
        seq = base_no + (i // len(airlines)) * 2 + (1 if direction == "return" else 0)
        flight_no = f"{code}{seq}"
        depart_time = f"{hour:02d}:00"
        arrive_total_min = hour * 60 + duration
        arrive_time = f"{(arrive_total_min // 60) % 24:02d}:{arrive_total_min % 60:02d}"
        price = _flight_price(f"{city}-{flight_no}-{on_date}", hour)
        from_airport, to_airport = (bjs_airport, city) if direction == "depart" else (dest_airport, bjs_airport)
        routes.append({
            "airline": name, "flight_no": flight_no, "from_": from_airport, "to": to_airport,
            "depart_time": depart_time, "arrive_time": arrive_time, "price": price,
        })
    return routes


def _parse_date_range(date_range: str | None) -> tuple[str | None, str | None, int]:
    """把 "开始日期~结束日期" 拆成 (check_in, check_out, 晚数)；解析不出来就全部返回 None/默认晚数。"""
    if not date_range or "~" not in date_range:
        return None, None, _DEFAULT_STAY_NIGHTS
    check_in, check_out = (s.strip() for s in date_range.split("~", 1))
    try:
        nights = (datetime.fromisoformat(check_out).date() - datetime.fromisoformat(check_in).date()).days
        return check_in, check_out, max(1, nights)
    except ValueError:
        return check_in, check_out, _DEFAULT_STAY_NIGHTS


_ANCHOR_SANITY_KM = 50  # RollingGo 按地点名字匹配也可能像高德一样认错地方（生僻地名尤其容易
# 撞到国外同名地方——真实测过"中西药局旧址"这种没那么出名的地标被匹配成美国圣路易斯，坐标
# 直接跑去了密苏里州），查回来的酒店离锚点真实坐标这么远就当匹配失败，整批放弃退回城市级搜索


def _haversine_km(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """跟 route_agent.py/schedule_widgets.py 的同名公式一样，独立一份不建跨模块依赖。"""
    import math

    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = rlat2 - rlat1, math.radians(lng2 - lng1)
    return 6371 * 2 * math.asin(min(1, math.sqrt(math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2)))


def _days_last_stops(trip_plan_obj: dict) -> list[dict]:
    """取每天最后一个带坐标的节点——晚上回酒店睡觉，第二天早上从酒店出发，"最后一站离酒店
    远不远"比"第一站"更直接影响住宿体验，酒店锚点/距离复核都基于这个。返回
    [{"date":,"place":,"lng":,"lat":}, ...]，某天全部节点都没坐标（理论上不该发生）就跳过
    那一天，不报错。"""
    stops = []
    for day_date in sorted(trip_plan_obj.get("days", {})):
        for stop in reversed(trip_plan_module.day_stops(trip_plan_obj["days"][day_date])):
            if stop.get("place") and stop.get("lng") is not None and stop.get("lat") is not None:
                stops.append({"date": day_date, "place": stop["place"], "lng": stop["lng"], "lat": stop["lat"]})
                break
    return stops


def _anchor_place_from_trip(trip_plan_obj: dict) -> dict | None:
    """从已经排好的行程里算一个"重心地点"给酒店搜索当锚点——取所有天数最后一站坐标的
    平均值（2026-09-15 起，不再只看某一天），这样搜出来的候选贴着整趟行程的重心，不是
    只照顾其中一天、也不是漫无目的地搜整个城市。RollingGo 的 place 参数要一个真实地名做
    文字检索（不接受裸坐标），用离重心最近的那一站的地点名字当查询文字，但返回的坐标本身
    是重心点——后面 _search_real_hotels() 的合理性校验会更准确地反映"整趟行程"，不是
    "某一天"。

    背景：真实 demo 演示时发现，编排 Agent 组句的自由文本会"聪明地"根据行程位置建议住哪个
    区域（比如"你的行程都在新马路一带，别住氹仔"），但那只是模型看着 JSON context 自由发挥，
    底下真正查酒店、真正会出现在 hotel_picker 卡片让用户选的候选，之前一直不管行程位置、
    只按整个城市搜——嘴上说的和真正能选的不是一回事，用户点卡片照样能选中文字里劝退的
    氹仔酒店。真实验证过：hotel_tool.search_hotels() 传 place="大三巴", place_type="景点"
    （而不是 place=城市, place_type="城市"）确实会把氹仔酒店排除出候选。

    没有排任何行程（比如用户还没问过内容推荐、直接先问订酒店）就返回 None，调用方退回
    城市级搜索，不强求。
    """
    last_stops = _days_last_stops(trip_plan_obj)
    if not last_stops:
        return None
    avg_lng = sum(s["lng"] for s in last_stops) / len(last_stops)
    avg_lat = sum(s["lat"] for s in last_stops) / len(last_stops)
    nearest = min(last_stops, key=lambda s: _haversine_km(avg_lng, avg_lat, s["lng"], s["lat"]))
    return {"place": nearest["place"], "lng": avg_lng, "lat": avg_lat}


_HOTEL_CONVENIENCE_KM = 5  # 晚上回酒店，跟当天最后一站的距离在这个范围内算"不算特别远，不用换"


def _existing_hotel_distance_check(trip_plan_obj: dict) -> str | None:
    """已经订好酒店（trip_plan.hotels 非空）时，检查现有酒店离每天最后一站够不够近。都在
    _HOTEL_CONVENIENCE_KM 内就返回 None（不用提醒）；某天超出了，返回一句提醒文字——不自动
    重新搜索/推荐换酒店，酒店是用户已经确认预订的真实决定，系统不该擅自建议换，只在回复里
    如实提醒，让用户自己决定要不要再查。酒店没有坐标（比如手填地址查不到，或者是坐标透传
    修好之前订的老数据）就跳过检查，不报错。"""
    hotels = trip_plan_obj.get("hotels", [])
    if not hotels:
        return None
    hotel = hotels[0]
    hotel_lng, hotel_lat = hotel.get("lng"), hotel.get("lat")
    if hotel_lng is None or hotel_lat is None:
        return None
    far_days = [
        s for s in _days_last_stops(trip_plan_obj)
        if _haversine_km(hotel_lng, hotel_lat, s["lng"], s["lat"]) > _HOTEL_CONVENIENCE_KM
    ]
    if not far_days:
        return None
    names = "、".join(f"{s['date']}（{s['place']}）" for s in far_days)
    return f"已预订的酒店「{hotel.get('name')}」离 {names} 这天的行程终点比较远，往返需要多预留交通时间。"


def _search_real_hotels(
    city: str, check_in: str | None, check_out: str | None, nights: int, category: str | None,
    user_message: str | None, anchor: dict | None = None, star_min: float | None = None,
) -> tuple[list[dict], str | None]:
    """真查 hotel_tool（道旅 RollingGo），失败时优雅降级返回空列表 + 错误信息，不往上抛异常炸穿 orchestrate()。

    anchor 给了（行程已经排出具体地点，{"place":,"lng":,"lat":}）就按这个地点搜
    （place_type="景点"，真实验证过这个类型能让 RollingGo 按地点收窄结果，不是只在文字里
    提一句），没给就退回城市级搜索（place_type="城市"）。

    star_min 透传给 hotel_tool.search_hotels()（那边接口本来就有这个参数，之前没接上）——
    trip_preferences.py 的 hotel_preference="度假酒店" 场景会传一个较高的星级门槛，偏向
    更好的酒店，不是随便哪家都行。

    锚点搜索有个真实踩过的坑：地标不够出名时 RollingGo 可能整个匹配错地方（"中西药局旧址"
    被匹配到美国圣路易斯去了）——查回来的酒店坐标用 anchor 里已经查好的真实坐标做一次合理性
    校验（_ANCHOR_SANITY_KM 内），全部超出范围就当这次锚点匹配失败，退回城市级搜索重查一次，
    不把跑偏的结果直接给用户。
    """
    origin_query = user_message or (f"想在{city}订一间{category}酒店" if category else f"想在{city}订一间性价比高的酒店")
    if anchor:
        origin_query += f"，希望离「{anchor['place']}」附近近一点，方便行程"
    place, place_type = (anchor["place"], "景点") if anchor else (city, "城市")
    try:
        hotels = hotel_tool.search_hotels(
            place=place, place_type=place_type, origin_query=origin_query,
            check_in_date=check_in, stay_nights=nights, size=5, star_min=star_min,
        )
    except Exception as e:
        return [], str(e)

    if anchor and hotels:
        on_target = [
            h for h in hotels
            if h.get("lng") is not None and h.get("lat") is not None
            and _haversine_km(anchor["lng"], anchor["lat"], h["lng"], h["lat"]) <= _ANCHOR_SANITY_KM
        ]
        if not on_target:
            try:
                hotels = hotel_tool.search_hotels(
                    place=city, place_type="城市", origin_query=origin_query,
                    check_in_date=check_in, stay_nights=nights, size=5,
                )
            except Exception as e:
                return [], str(e)

    if not check_in:
        check_in = (date.today() + timedelta(days=1)).isoformat()
    if not check_out:
        check_out = (datetime.fromisoformat(check_in).date() + timedelta(days=nights)).isoformat()

    candidates = [
        {
            "name": h["name"],
            "address": h.get("address"),
            "check_in": check_in,
            "check_out": check_out,
            "price": h.get("price_per_night"),
            "cancel_policy": "以具体房型页面显示为准（不同房型取消政策不同，选定酒店后可再查房型明细）",
            "rating": h.get("star_rating"),
            "provider_type": "hotel",
            # 下面几个是真实数据里额外带的字段，widgets.py/前端目前没强制要求，但留着方便以后展示
            "hotel_id": h.get("hotel_id"),
            "image_url": h.get("image_url"),
            "booking_url": h.get("booking_url"),
            "amenities": h.get("amenities", []),
            # 真实坐标（hotel_tool.search_hotels() 本来就有），存进候选顺手带出去——
            # server.py 的 apply_selection() 确认预订时会存进 trip_plan.hotels，
            # 后面"已订酒店离行程远不远"的距离复核（_existing_hotel_distance_check()）要用
            "lng": h.get("lng"),
            "lat": h.get("lat"),
        }
        for h in hotels
    ]
    return candidates, None


def run(
    shared_state: dict,
    location: str | None,
    date_range: str | None = None,
    category: str | None = None,
    user_message: str | None = None,
) -> dict:
    """
    location 没传就用 shared_state["city"]（跟 route_agent.py 等其他 Agent 一样的取城市方式），
    再没有就兜底"澳门"（demo 默认城市，见 server.py 的 _DEMO_CITY）。
    user_message 传了会原样喂给 hotel_tool 当查询意图描述（比关键词拼出来的更准），
    orchestrator_agent.py 里 booking 意图命中时会把这轮用户消息传进来。

    2026-09-15 起会先从 shared_state["trip_plan"] 里取一个已排行程的锚点地点
    （_anchor_place_from_trip()，所有天数最后一站坐标的重心），酒店搜索按这个地点收窄
    （见 _search_real_hotels()），不再是不管行程位置、无差别搜整个城市。

    已经订好酒店（trip_plan.hotels 非空）就不再搜新的酒店候选——用户已经做过真实预订
    决定，不该每次 booking 意图命中都重新搜一遍候选去"暗示"换酒店；改成调
    _existing_hotel_distance_check() 检查现有酒店离行程够不够近，有问题只在结果里带一句
    提醒（result["hotel_distance_reminder"]），不自动推荐替代方案。

    trip_preferences.py 的 hotel_preference="度假酒店" 时（用户想单独挑一家好酒店当度假
    体验，不介意离行程远）不走锚点收窄，搜城市级候选、且提高星级门槛；"周边"/没问过（默认）
    时维持锚点收窄的行为不变。
    """
    city = location or shared_state.get("city") or "澳门"
    check_in, check_out, nights = _parse_date_range(date_range)
    trip_plan_obj = shared_state.get("trip_plan", {})
    hotel_preference = shared_state.get("trip_preferences", {}).get("hotel_preference")

    hotel_distance_reminder = _existing_hotel_distance_check(trip_plan_obj)
    if trip_plan_obj.get("hotels"):
        hotel_candidates, hotel_error = [], None
    elif hotel_preference == "度假酒店":
        resort_query = user_message or f"想在{city}找一家适合度假体验的高品质酒店"
        hotel_candidates, hotel_error = _search_real_hotels(
            city, check_in, check_out, nights, category, resort_query, anchor=None, star_min=4.0
        )
    else:
        anchor = _anchor_place_from_trip(trip_plan_obj)
        hotel_candidates, hotel_error = _search_real_hotels(city, check_in, check_out, nights, category, user_message, anchor)

    # 去程用入住日期，回程用离店日期——往返机票配对，不是只有单程
    depart_date = check_in or (date.today() + timedelta(days=1)).isoformat()
    return_date = check_out or (datetime.fromisoformat(depart_date).date() + timedelta(days=nights)).isoformat()
    flight_candidates = [
        {
            "name": f"{r['flight_no']} {r['from_']}→{r['to']}",
            "flight_no": r["flight_no"],
            "from_": r["from_"],
            "to": r["to"],
            "date": on_date,
            "depart_time": r["depart_time"],
            "arrive_time": r["arrive_time"],
            "status": "on_time",
            "price": r["price"],
            "inventory": 9,
            "rating": 4.6,
            "provider_type": "flight",
        }
        for direction, on_date in (("depart", depart_date), ("return", return_date))
        for r in _generate_beijing_flights(city, on_date, direction)
    ]

    # flight_priority="省钱" 时按价格升序排一下——机票是假数据（见文件顶部说明），排序是
    # 目前唯一能真实影响的地方，不假装能筛真实时刻/舱位。"折衷"/"极致体验" 维持原有的
    # 按时段排列，不特殊处理（假数据里没有能区分"体验"好坏的字段，不硬凑）。
    if shared_state.get("trip_preferences", {}).get("flight_priority") == "省钱":
        flight_candidates.sort(key=lambda f: f["price"])

    result = {"candidates": flight_candidates + hotel_candidates}
    if hotel_error:
        result["hotel_query_error"] = hotel_error
    if hotel_distance_reminder:
        result["hotel_distance_reminder"] = hotel_distance_reminder
    return result


if __name__ == "__main__":
    import json

    for demo_city in ("澳门", "香港"):
        print(f"--- {demo_city} ---")
        print(json.dumps(
            run(shared_state={"city": demo_city}, location=None, date_range="2026-09-15~2026-09-17"),
            ensure_ascii=False, indent=2,
        ))
