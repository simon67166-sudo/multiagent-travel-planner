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


def _search_real_hotels(city: str, check_in: str | None, check_out: str | None, nights: int, category: str | None, user_message: str | None) -> tuple[list[dict], str | None]:
    """真查 hotel_tool（道旅 RollingGo），失败时优雅降级返回空列表 + 错误信息，不往上抛异常炸穿 orchestrate()。"""
    origin_query = user_message or (f"想在{city}订一间{category}酒店" if category else f"想在{city}订一间性价比高的酒店")
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
    """
    city = location or shared_state.get("city") or "澳门"
    check_in, check_out, nights = _parse_date_range(date_range)

    hotel_candidates, hotel_error = _search_real_hotels(city, check_in, check_out, nights, category, user_message)

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

    result = {"candidates": flight_candidates + hotel_candidates}
    if hotel_error:
        result["hotel_query_error"] = hotel_error
    return result


if __name__ == "__main__":
    import json

    for demo_city in ("澳门", "香港"):
        print(f"--- {demo_city} ---")
        print(json.dumps(
            run(shared_state={"city": demo_city}, location=None, date_range="2026-09-15~2026-09-17"),
            ensure_ascii=False, indent=2,
        ))
