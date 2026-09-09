import json
import math


RESTAURANTS = [
    {
        "name": "演示餐廳 A",
        "price_mop": 65,
        "non_spicy_available": True,
        "queue_minutes": 10,
    },
    {
        "name": "演示餐廳 B",
        "price_mop": 95,
        "non_spicy_available": True,
        "queue_minutes": 45,
    },
    {
        "name": "演示餐廳 C",
        "price_mop": 55,
        "non_spicy_available": False,
        "queue_minutes": 5,
    },
]


def valid_number(value, maximum):
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and 0 <= value <= maximum
    )


def search_demo_restaurants(
    max_price_mop,
    require_non_spicy,
    max_queue_minutes,
):
    if not valid_number(max_price_mop, 10000):
        raise ValueError("預算必須是 0 到 10000 之間的有限數字")

    if type(require_non_spicy) is not bool:
        raise ValueError("require_non_spicy 必須是布林值")

    if (
        max_queue_minutes is not None
        and not valid_number(max_queue_minutes, 1440)
    ):
        raise ValueError("排隊上限必須是 0 到 1440 的數字或 null")

    eligible = []
    excluded = []

    for restaurant in RESTAURANTS:
        reasons = []

        if restaurant["price_mop"] > max_price_mop:
            reasons.append("超出人均預算")

        if require_non_spicy and not restaurant["non_spicy_available"]:
            reasons.append("沒有不辣選項")

        if (
            max_queue_minutes is not None
            and restaurant["queue_minutes"] > max_queue_minutes
        ):
            reasons.append("超出排隊時間上限")

        if reasons:
            excluded.append(
                {
                    "name": restaurant["name"],
                    "reasons": reasons,
                }
            )
        else:
            eligible.append(restaurant)

    return {
        "is_mock": True,
        "currency": "MOP",
        "constraints": {
            "max_price_mop": max_price_mop,
            "require_non_spicy": require_non_spicy,
            "max_queue_minutes": max_queue_minutes,
        },
        "eligible": eligible,
        "excluded": excluded,
    }


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_demo_restaurants",
            "description": (
                "查詢虛構澳門餐廳，按預算、飲食及排隊限制篩選。"
                "使用同一對話中最新的需求。"
                "預算不明時先追問，不可自行設定。"
                "未提出不辣要求時 require_non_spicy 為 false；"
                "未提出排隊上限時 max_queue_minutes 為 null。"
                "只能推薦 eligible 中的餐廳。"
                "excluded 僅用來說明排除原因。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "max_price_mop": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 10000,
                        "description": "人均澳門元預算上限",
                    },
                    "require_non_spicy": {
                        "type": "boolean",
                        "description": "是否必須提供不辣選項",
                    },
                    "max_queue_minutes": {
                        "type": ["number", "null"],
                        "minimum": 0,
                        "maximum": 1440,
                        "description": "排隊上限分鐘數，未指定時為 null",
                    },
                },
                "required": [
                    "max_price_mop",
                    "require_non_spicy",
                    "max_queue_minutes",
                ],
                "additionalProperties": False,
            },
        },
    }
]


def execute_tool(call):
    if call.function.name != "search_demo_restaurants":
        return {"error": "未知工具"}

    try:
        arguments = json.loads(call.function.arguments)
    except (json.JSONDecodeError, TypeError):
        return {"error": "工具參數不是有效 JSON"}

    expected = {
        "max_price_mop",
        "require_non_spicy",
        "max_queue_minutes",
    }

    if not isinstance(arguments, dict) or set(arguments) != expected:
        return {"error": "必須提供且只能提供預算、飲食、排隊三個參數"}

    try:
        return search_demo_restaurants(**arguments)
    except ValueError as error:
        return {"error": str(error)}