"""
OTA/酒店 Agent -- 查库存/比价。

模型档位：MODEL_LIGHT（结构化查库存比价，轻量模型）。

现状：**酒店这半边已经接了真实数据**（`hotel_tool.py`，道旅 RollingGo 的真实酒店库存/价格，
2026-09-13 接入），机票这半边还是纯占位假数据（还没有免费的个人开发者可用的真实机票比价来源）。

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

    # 机票这半边还没有真实数据来源（个人开发者拿不到免费 key，见 docs 待接事项），
    # 目的地跟着城市走，至少不会跟真实酒店数据出现在同一个候选列表里时明显对不上
    flight_candidates = [
        {
            "name": f"MU5137 上海虹桥→{city}",
            "flight_no": "MU5137",
            "from_": "上海虹桥国际机场",
            "to": city,
            "depart_time": "08:00",
            "arrive_time": "10:10",
            "status": "on_time",
            "price": 890,
            "inventory": 12,
            "rating": 4.8,
            "provider_type": "flight",
        }
    ]

    result = {"candidates": flight_candidates + hotel_candidates}
    if hotel_error:
        result["hotel_query_error"] = hotel_error
    return result


if __name__ == "__main__":
    import json

    print(json.dumps(run(shared_state={"city": "澳门"}, location=None, date_range="2026-09-20~2026-09-22"), ensure_ascii=False, indent=2))
