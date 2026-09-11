"""
OTA/酒店 Agent -- 查库存/比价。

模型档位：MODEL_LIGHT（结构化查库存比价，轻量模型）。

现状：**酒店这半边已经接了真实数据**（`hotel_tool.py`，道旅 RollingGo 的真实酒店库存/价格，
2026-09-13 接入），机票这半边还是占位假数据——调研过一圈（携程/去哪儿企业合作制、Kiwi/Amadeus
关闭自助注册、Travelpayouts 要联盟审核+实时数据要求 5 万月活、FlightAPI.io 只有 20 次免费调用），
没有真正免费的个人开发者可用来源，也明确决定不用爬虫方案（绕开正常接口抓商业网站数据，大概率
违反对方服务条款，demo 用途也不例外）。折中方案：`_BEIJING_FLIGHT_ROUTES` 按真实通航航司/
真实航班号格式编了北京→港澳的示例数据（航班本身是编的，不代表真实存在的具体航班），至少比之前
"上海虹桥→杭州萧山"这种跟港澳 demo 完全对不上的老占位数据靠谱一些。等 RollingGo 的机票 MCP
恢复（見文末待接事项）再换成真实数据。

字段补全到"确认预订后能直接落地"需要的程度（机票带 flight_no/from_/to/depart_time/arrive_time，
酒店带 address/check_in/check_out）——这样 server.py 的 POST /widget-response 收到用户选中的候选后，
能直接拿这些字段调 trip_plan.add_flight()/add_hotel()，不用现造字段。date_range 传了就用来填
check_in/check_out，格式 "开始日期~结束日期"。
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

import hotel_tool

_DEFAULT_STAY_NIGHTS = 2

# 机票候选还是没有真实数据源（见文件顶部说明），但至少让航司/航班号贴近真实航线，不是随手编的
# 上海虹桥→杭州萧山那种跟港澳 demo 完全对不上的老数据。价格/余票是演示用途虚构值，不是实时报价。
# 数据来源：2026-09-13 查了北京首都/大兴机场到香港/澳门的真实通航航司（国航/国泰/港航/海航 飞香港，
# 南航/国航/东航 飞澳门），航班号是按真实航司二字码编的示例号，不代表某一趟具体真实航班。
_BEIJING_FLIGHT_ROUTES = {
    "香港": [
        {"airline": "中国国际航空", "flight_no": "CA111", "from_": "北京首都国际机场", "depart_time": "08:30", "arrive_time": "11:55", "price": 1580},
        {"airline": "国泰航空", "flight_no": "CX330", "from_": "北京首都国际机场", "depart_time": "13:10", "arrive_time": "16:35", "price": 1720},
        {"airline": "香港航空", "flight_no": "HX330", "from_": "北京大兴国际机场", "depart_time": "19:00", "arrive_time": "22:25", "price": 1350},
    ],
    "澳门": [
        {"airline": "中国南方航空", "flight_no": "CZ3505", "from_": "北京首都国际机场", "depart_time": "09:05", "arrive_time": "12:35", "price": 1420},
        {"airline": "中国国际航空", "flight_no": "CA1305", "from_": "北京大兴国际机场", "depart_time": "14:20", "arrive_time": "17:50", "price": 1500},
        {"airline": "中国东方航空", "flight_no": "MU2951", "from_": "北京首都国际机场", "depart_time": "20:15", "arrive_time": "23:45", "price": 1280},
    ],
}


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

    flight_date = check_in or (date.today() + timedelta(days=1)).isoformat()
    routes = _BEIJING_FLIGHT_ROUTES.get(city, [
        {"airline": "中国东方航空", "flight_no": "MU5137", "from_": "上海虹桥国际机场", "depart_time": "08:00", "arrive_time": "10:10", "price": 890},
    ])
    flight_candidates = [
        {
            "name": f"{r['flight_no']} {r['from_']}→{city}",
            "flight_no": r["flight_no"],
            "from_": r["from_"],
            "to": city,
            "date": flight_date,
            "depart_time": r["depart_time"],
            "arrive_time": r["arrive_time"],
            "status": "on_time",
            "price": r["price"],
            "inventory": 9,
            "rating": 4.6,
            "provider_type": "flight",
        }
        for r in routes
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
