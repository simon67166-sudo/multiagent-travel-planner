import json
import math


RESTAURANTS = [
    {
        "name": "演示餐厅 A",
        "price_mop": 65,
        "non_spicy_available": True,
        "queue_minutes": 10,
    },
    {
        "name": "演示餐厅 B",
        "price_mop": 95,
        "non_spicy_available": True,
        "queue_minutes": 45,
    },
    {
        "name": "演示餐厅 C",
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
        raise ValueError("预算必须是 0 到 10000 之间的有限数字")

    if type(require_non_spicy) is not bool:
        raise ValueError("require_non_spicy 必须是布林值")

    if (
        max_queue_minutes is not None
        and not valid_number(max_queue_minutes, 1440)
    ):
        raise ValueError("排队上限必须是 0 到 1440 的数字或 null")

    eligible = []
    excluded = []

    for restaurant in RESTAURANTS:
        reasons = []

        if restaurant["price_mop"] > max_price_mop:
            reasons.append("超出人均预算")

        if require_non_spicy and not restaurant["non_spicy_available"]:
            reasons.append("没有不辣选项")

        if (
            max_queue_minutes is not None
            and restaurant["queue_minutes"] > max_queue_minutes
        ):
            reasons.append("超出排队时间上限")

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
                "查询虚构澳门餐厅，按预算、饮食及排队限制筛选。"
                "使用同一对话中最新的需求。"
                "预算不明时先追问，不可自行设定。"
                "未提出不辣要求时 require_non_spicy 为 false；"
                "未提出排队上限时 max_queue_minutes 为 null。"
                "只能推荐 eligible 中的餐厅。"
                "excluded 仅用来说明排除原因。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "max_price_mop": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 10000,
                        "description": "人均澳门元预算上限",
                    },
                    "require_non_spicy": {
                        "type": "boolean",
                        "description": "是否必须提供不辣选项",
                    },
                    "max_queue_minutes": {
                        "type": ["number", "null"],
                        "minimum": 0,
                        "maximum": 1440,
                        "description": "排队上限分钟数，未指定时为 null",
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
        return {"error": "工具参数不是有效 JSON"}

    expected = {
        "max_price_mop",
        "require_non_spicy",
        "max_queue_minutes",
    }

    if not isinstance(arguments, dict) or set(arguments) != expected:
        return {"error": "必须提供且只能提供预算、饮食、排队三个参数"}

    try:
        return search_demo_restaurants(**arguments)
    except ValueError as error:
        return {"error": str(error)}