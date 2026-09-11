"""
道旅 RollingGo 酒店工具 -- 真实酒店搜索 + 房型报价，给 OTA/酒店 Agent 用。
跟 map_tool.py/weather_tool.py 一样是通用基础设施，跟"编排"本身无关，谁都能 import。

依赖：pip install requests
运行前准备：在 orchestrator/.env 里加两行
  ROLLINGGO_MCP_URL=https://mcp.rollinggo.cn/mcp
  ROLLINGGO_API_KEY=你的道旅 RollingGo API Key
（去 https://travelportal-partner-center.dida.com/register?lang=zh 免费申请，个人/企业开发者均可，
0 等待即时拿到 key；官方说明目前阶段免费使用、无调用上限，但后续可能调整，以官方页面为准）

协议说明：这个服务本质是个 MCP（Model Context Protocol）server，不是普通 REST API，但可以直接用
requests 发 JSON-RPC 2.0 请求过去，不用接一整套 MCP 客户端框架——method 固定传 "tools/call"，
params.name 是工具名（searchHotels/getHotelDetail），params.arguments 是工具参数；真正的业务数据
包了两层：response["result"]["content"][0]["text"] 是一段 JSON 字符串，要再解析一次。
请求头必须带 "Accept: application/json, text/event-stream"，缺了服务端直接返回 400。

只做只读查询（搜酒店、查房型报价），不碰真实下单/支付——那部分官方是另一个交互式命令行工具
（rgh 系列，走 OAuth 登录 + 人工二次确认），不适合塞进这种自动跑的后端 Agent 流程，本项目不接。
"""

import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _get_config() -> tuple[str, str]:
    url = os.environ.get("ROLLINGGO_MCP_URL")
    key = os.environ.get("ROLLINGGO_API_KEY")
    if not url or not key:
        raise RuntimeError(
            "环境变量 ROLLINGGO_MCP_URL/ROLLINGGO_API_KEY 未设置，"
            "先去 https://travelportal-partner-center.dida.com/register?lang=zh 申请免费 key 再用。"
        )
    return url, key


def _call_tool(name: str, arguments: dict) -> dict:
    url, key = _get_config()
    resp = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {key}",
        },
        data=json.dumps({"jsonrpc": "2.0", "method": "tools/call", "params": {"name": name, "arguments": arguments}, "id": 1}),
        timeout=20,
    )
    data = resp.json()
    if "error" in data:
        raise ValueError(f"RollingGo 酒店接口报错: {name} -> {data['error']}")
    inner = json.loads(data["result"]["content"][0]["text"])
    if not inner.get("success"):
        raise ValueError(f"RollingGo 酒店查询失败: {name} -> {inner}")
    return inner


def search_hotels(
    place: str,
    place_type: str,
    origin_query: str,
    check_in_date: str | None = None,
    stay_nights: int = 1,
    star_min: float | None = None,
    size: int = 5,
) -> list[dict]:
    """
    搜酒店候选，返回列表，每条：
    {"hotel_id", "name", "name_en", "address", "star_rating", "price_per_night", "currency",
     "price_message", "lat", "lng", "image_url", "booking_url", "amenities", "tags"}

    place/place_type/origin_query 是 RollingGo 自己 schema 里的必填项：
    - place：地名本身（比如"大三巴"/"澳门"）
    - place_type：说明 place 是什么类型（城市/机场/景点/火车站/地铁站/酒店/区县/详细地址）
    - origin_query：一句完整的中文查询意图描述（不是关键词），比如"想在大三巴附近订一间性价比高的四星酒店"

    price_per_night 是从搜索结果的 lowest_price（整个入住期间总价）按 stay_nights 换算出来的估算值，
    不是单独再查一次逐晚价格——想要精确到"具体房型每晚多少钱"，用 get_hotel_detail() 再查一次。
    """
    args = {"originQuery": origin_query, "place": place, "placeType": place_type, "size": min(20, size)}
    check_in: dict = {}
    if check_in_date:
        check_in["checkInDate"] = check_in_date
    if stay_nights:
        check_in["stayNights"] = stay_nights
    if check_in:
        args["checkInParam"] = check_in
    if star_min is not None:
        args["filterOptions"] = {"starRatings": [star_min, 5.0]}

    data = _call_tool("searchHotels", args)
    nights = max(1, stay_nights)
    return [
        {
            "hotel_id": h.get("hotelId"),
            "name": h.get("name"),
            "name_en": h.get("nameEn"),
            "address": h.get("address"),
            "star_rating": h.get("starRating"),
            "price_per_night": round((h.get("price") or {}).get("lowestPrice", 0) / nights, 2)
            if (h.get("price") or {}).get("hasPrice")
            else None,
            "currency": (h.get("price") or {}).get("currency"),
            "price_message": (h.get("price") or {}).get("message"),
            "lat": h.get("latitude"),
            "lng": h.get("longitude"),
            "image_url": h.get("imageUrl"),
            "booking_url": h.get("bookingUrl"),
            "amenities": h.get("hotelAmenities", []),
            "tags": h.get("tags", []),
        }
        for h in data.get("hotelInformationList", [])
    ]


def get_hotel_detail(
    hotel_id: int, check_in_date: str, check_out_date: str, adult_count: int = 2, room_count: int = 1
) -> list[dict]:
    """
    查某个酒店的具体房型报价，返回房型列表，每条：
    {"room_name", "rate_plan_id", "price_per_night", "currency", "bed_type", "meal", "cancelable", "cancel_policy"}
    hotel_id 从 search_hotels() 结果的 hotel_id 拿。
    """
    data = _call_tool(
        "getHotelDetail",
        {
            "hotelId": hotel_id,
            "dateParam": {"checkInDate": check_in_date, "checkOutDate": check_out_date},
            "occupancyParam": {"adultCount": adult_count, "roomCount": room_count},
        },
    )
    return [
        {
            "room_name": r.get("roomName"),
            "rate_plan_id": r.get("ratePlanId"),
            "price_per_night": r.get("averagePrice"),
            "currency": r.get("currency"),
            "bed_type": r.get("bedTypeDescription"),
            "meal": r.get("mealTypeStr"),
            "cancelable": r.get("cancelable"),
            "cancel_policy": r.get("cancelPolicy"),
        }
        for r in data.get("roomRatePlans", [])
    ]


if __name__ == "__main__":
    hotels = search_hotels("大三巴", "景点", "想在大三巴附近订一间四星左右的酒店", check_in_date="2026-09-20", stay_nights=2, size=3)
    print(json.dumps(hotels, ensure_ascii=False, indent=2))
    if hotels:
        rooms = get_hotel_detail(hotels[0]["hotel_id"], "2026-09-20", "2026-09-22")
        print(json.dumps(rooms, ensure_ascii=False, indent=2))
