"""
旅游计划模块 -- 由多个 Agent 共同维护，最终用于呈现（聊天回复 + 地图/行程面板）

覆盖范围：行程（景点/通勤/三餐夜宵）、机票、酒店、天气异常。

维护关系（谁写哪块）：
  flights        -- OTA/酒店 Agent
  hotels         -- OTA/酒店 Agent
  weather_alerts -- 异常应变 Agent
  days（景点通勤+餐饮）-- 行程/路线 Agent 生成，异常应变 Agent 触发时增删/调整节点

景点通勤+餐饮的数据结构：
  每天一个链表，但不用指针式链表（Python 对象 .next 引用），而是"节点字典 + id 引用"：
  day_plan = {
    "date": "2026-10-01",
    "head_id": "node-1",          # 链表头节点 id
    "nodes": {
      "node-1": {
        "type": "attraction",       # "attraction" | "meal"
        "place": "西湖",
        "arrival_transport": "地铁",
        "arrival_time": "09:00",
        "end_time": "11:30",
        "next_id": "node-2",
      },
      "node-2": {
        "type": "meal",
        "meal_type": "午餐",         # meal 节点专属：早餐|午餐|晚餐|夜宵
        "place": "楼外楼",
        "arrival_transport": "步行",
        "arrival_time": "11:40",
        "end_time": "12:30",
        "next_id": None,
      },
    },
  }
  这样设计的原因：异常应变 Agent 改动行程时，只需要新增/删除/重连某一个节点的 next_id，
  不用重发整天的数组；同时它本质是个普通 dict，能直接存 JSON/传 HTTP，不需要额外的
  链表↔数组转换逻辑。渲染/前端展示时用 day_stops() 从 head_id 顺着 next_id 走一遍还原成
  有序数组即可。

人格/知识库仍待定义，本文件不涉及，只管行程本身的结构和增删改查。
"""

from typing import Any


# ---------------------------------------------------------------------------
# 顶层旅游计划
# ---------------------------------------------------------------------------
def new_trip_plan(trip_id: str) -> dict:
    return {
        "trip_id": trip_id,
        "flights": [],
        "hotels": [],
        "weather_alerts": [],
        "days": {},  # date -> day_plan
    }


def add_flight(trip_plan: dict, flight: dict[str, Any]) -> None:
    """flight 字段：flight_no, from_, to, date, depart_time, arrive_time,
    status(on_time|delayed|cancelled), booking_ref。date 是这趟航班的日期（"YYYY-MM-DD"）
    ——route_agent.schedule() 的 _flight_arrival_and_departure() 只用 from_/to 判断方向、
    不看 date，这个字段目前主要是给前端展示用，不参与排班判断。"""
    trip_plan["flights"].append(flight)


def add_hotel(trip_plan: dict, hotel: dict[str, Any]) -> None:
    """hotel 字段：name, check_in, check_out, address, booking_ref, price, cancel_policy"""
    trip_plan["hotels"].append(hotel)


def add_weather_alert(trip_plan: dict, alert: dict[str, Any]) -> None:
    """alert 字段：date, location, event_type(暴雨|台风|...), severity, description, affects_dates: [date]"""
    trip_plan["weather_alerts"].append(alert)


def get_or_create_day(trip_plan: dict, date: str) -> dict:
    if date not in trip_plan["days"]:
        trip_plan["days"][date] = {"date": date, "head_id": None, "nodes": {}}
    return trip_plan["days"][date]


# ---------------------------------------------------------------------------
# 景点通勤 + 餐饮：单日链表（节点字典 + id 引用）
# ---------------------------------------------------------------------------
def add_stop(
    day_plan: dict,
    node_id: str,
    type_: str,
    place: str,
    arrival_transport: str,
    arrival_time: str,
    end_time: str,
    after_id: str | None = None,
    **extra: Any,
) -> None:
    """
    插入一个节点。after_id=None 表示插到当天最前面，否则插到 after_id 节点后面。
    type_="meal" 时可以在 extra 里传 meal_type="早餐"/"午餐"/"晚餐"/"夜宵"。
    """
    node = {
        "type": type_,
        "place": place,
        "arrival_transport": arrival_transport,
        "arrival_time": arrival_time,
        "end_time": end_time,
        "next_id": None,
        **extra,
    }
    if after_id is None:
        node["next_id"] = day_plan["head_id"]
        day_plan["head_id"] = node_id
    else:
        after_node = day_plan["nodes"][after_id]
        node["next_id"] = after_node["next_id"]
        after_node["next_id"] = node_id
    day_plan["nodes"][node_id] = node


def remove_stop(day_plan: dict, node_id: str) -> None:
    """删除一个节点，自动重连前后节点。异常应变 Agent 常用（比如景点临时关闭）。"""
    prev_id = None
    cur_id = day_plan["head_id"]
    while cur_id is not None:
        if cur_id == node_id:
            break
        prev_id = cur_id
        cur_id = day_plan["nodes"][cur_id]["next_id"]

    removed = day_plan["nodes"].pop(node_id)
    if prev_id is None:
        day_plan["head_id"] = removed["next_id"]
    else:
        day_plan["nodes"][prev_id]["next_id"] = removed["next_id"]


def patch_stop(day_plan: dict, node_id: str, **fields: Any) -> None:
    """只改某个节点的部分字段（比如异常应变 Agent 把 arrival_transport 从地铁改成打车），不动链表结构。"""
    day_plan["nodes"][node_id].update(fields)


def cancel_stop_by_id(trip_plan: dict, node_id: str) -> dict | None:
    """
    按 node_id 取消日程里的一个节点，调用方不用先自己找是哪一天——遍历所有天定位到
    node_id 所在的 day_plan 再删。找不到返回 None；找到了返回被删掉的节点内容
    （多了 date/node_id 两个字段），方便调用方把"取消了什么"回复给用户。

    给编排 Agent 用：异常应变 Agent 的提案（见 agents/exception_agent.py）只负责发现
    "可能受影响的节点"，不直接执行；用户确认要取消之后，编排 Agent 拿着 node_id 调这个
    函数才是真正的执行动作。
    """
    for day_date, day_plan in trip_plan["days"].items():
        if node_id in day_plan["nodes"]:
            removed = dict(day_plan["nodes"][node_id])
            remove_stop(day_plan, node_id)
            removed["date"] = day_date
            removed["node_id"] = node_id
            return removed
    return None


def cancel_stops_by_place(trip_plan: dict, place: str) -> list[dict]:
    """
    按地点名字（精确匹配 place 字段）取消日程里所有同名节点，可能跨天/命中多个，返回
    被删掉的节点列表（每条带 date/node_id）。

    给编排 Agent 用：exception_agent.run()/check_weather() 提案里的 affected_locations
    是地点名字（不是 node_id），用户确认要取消之后，编排 Agent 拿着这些地点名字调这个
    函数执行——跟 cancel_stop_by_id() 是同一类"确认后执行"接口，按场景选用哪个。
    """
    removed = []
    for day_date, day_plan in trip_plan["days"].items():
        matched_ids = [nid for nid, node in day_plan["nodes"].items() if node.get("place") == place]
        for nid in matched_ids:
            node = dict(day_plan["nodes"][nid])
            remove_stop(day_plan, nid)
            node["date"] = day_date
            node["node_id"] = nid
            removed.append(node)
    return removed


def day_stops(day_plan: dict) -> list[dict]:
    """把链表还原成有序数组，供渲染/地图面板使用。"""
    stops = []
    cur_id = day_plan["head_id"]
    while cur_id is not None:
        node = dict(day_plan["nodes"][cur_id])
        node["node_id"] = cur_id
        stops.append(node)
        cur_id = node["next_id"]
    return stops


# ---------------------------------------------------------------------------
# 汇总渲染：给聊天回复/地图面板用的最终展示结构
# ---------------------------------------------------------------------------
def render(trip_plan: dict) -> dict:
    return {
        "trip_id": trip_plan["trip_id"],
        "flights": trip_plan["flights"],
        "hotels": trip_plan["hotels"],
        "weather_alerts": trip_plan["weather_alerts"],
        "days": [
            {"date": date, "stops": day_stops(day_plan)}
            for date, day_plan in sorted(trip_plan["days"].items())
        ],
    }


if __name__ == "__main__":
    import json

    plan = new_trip_plan("trip-demo-1")
    add_flight(
        plan,
        {"flight_no": "MU5137", "from_": "SHA", "to": "HGH", "depart_time": "08:00", "arrive_time": "09:10", "status": "on_time"},
    )
    add_hotel(plan, {"name": "西湖某酒店", "check_in": "2026-10-01", "check_out": "2026-10-03", "price": 599})

    day1 = get_or_create_day(plan, "2026-10-01")
    add_stop(day1, "n1", "attraction", "西湖", "地铁", "09:30", "11:30")
    add_stop(day1, "n2", "meal", "楼外楼", "步行", "11:40", "12:30", after_id="n1", meal_type="午餐")
    add_stop(day1, "n3", "attraction", "灵隐寺", "打车", "13:30", "15:30", after_id="n2")
    add_stop(day1, "n4", "meal", "宋城夜市", "打车", "20:00", "21:30", after_id="n3", meal_type="夜宵")

    print("--- 初始行程 ---")
    print(json.dumps(day_stops(day1), ensure_ascii=False, indent=2))

    # 模拟异常应变 Agent：灵隐寺临时闭园，删掉这一站，改用打车去下一站
    add_weather_alert(
        plan,
        {"date": "2026-10-01", "location": "灵隐寺", "event_type": "临时闭园", "severity": "中", "description": "维修临时闭园", "affects_dates": ["2026-10-01"]},
    )
    remove_stop(day1, "n3")
    patch_stop(day1, "n4", arrival_transport="打车")

    print("--- 异常应变后 ---")
    print(json.dumps(day_stops(day1), ensure_ascii=False, indent=2))

    print("--- 完整渲染结构 ---")
    print(json.dumps(render(plan), ensure_ascii=False, indent=2))
