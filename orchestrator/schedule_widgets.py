"""
左侧行程面板展示组件 -- 服务对象是左侧实时日程面板（server.py 的 GET /trip，
web/index.html 目前的文字列表），跟右侧聊天框的 widgets.py 是同一个"数据/展示分离"思路：
这里只算"给前端画图用的数据"，不产出任何 HTML/画布逻辑，真正怎么画留给前端。

设计原则：
- 数据来源必须是 trip_plan 里真实存在的行程节点，不允许编造；地点坐标用 map_tool.geocode()
  查真实经纬度，查不到就跳过该点并记进 geocode_failures，不让一个查询失败拖垮整张地图。
- 同一天的所有节点用同一个颜色标记，颜色按"这是第几天"分配，不是按地点类型分配。

模型档位：不需要 LLM——纯确定性计算（按天分配颜色 + 查真实经纬度）。
"""

import sys
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

import map_tool
import trip_plan as trip_plan_module

_DAY_COLOR_PALETTE = [
    "#1f6f54",  # 第1天：jade 绿
    "#b5573a",  # 第2天：赤陶
    "#2f6fed",  # 第3天：蓝
    "#c9a227",  # 第4天：金
    "#8654c9",  # 第5天：紫
    "#d6336c",  # 第6天：玫红
    "#0f9aa8",  # 第7天：青
    "#e07b39",  # 第8天：橙
]  # 超过 8 天就循环复用颜色（demo/比赛阶段行程不会真的排那么多天）


def _color_for_day(day_index: int) -> str:
    return _DAY_COLOR_PALETTE[day_index % len(_DAY_COLOR_PALETTE)]


def build_trip_map_widget(trip_plan_obj: dict, city: str | None = None) -> dict:
    """
    左侧日程面板的地图组件：把 trip_plan 里每天的行程节点标进地图，同一天用同一个颜色的图标。

    trip_plan_obj: shared_state["trip_plan"]（trip_plan.py 的原始结构，不是 render() 之后的）。
    city: 地理编码用的城市提示，同名地点在不同城市容易查错，传了更准（比如整趟行程都在杭州就传"杭州"）。

    返回 {"widget": "trip_map", "data": {"markers": [...], "days_legend": [...], "geocode_failures": [...]}}：
    - markers 每条：day（日期）/ color / place / node_id / type / lng / lat
    - days_legend：每天对应的颜色，前端拿这个画图例
    - geocode_failures：查不到坐标的地点（比如高德 key 没配、地名太模糊），前端可以提示"部分地点未能定位"
    """
    rendered = trip_plan_module.render(trip_plan_obj)
    markers = []
    days_legend = []
    geocode_failures = []
    geocode_cache: dict[str, tuple[float, float]] = {}

    for day_index, day in enumerate(rendered["days"]):
        color = _color_for_day(day_index)
        days_legend.append({"date": day["date"], "color": color})
        for stop in day["stops"]:
            place = stop.get("place")
            if not place:
                continue
            if place not in geocode_cache:
                try:
                    geocode_cache[place] = map_tool.geocode(place, city=city)
                except Exception as e:
                    geocode_failures.append({"place": place, "reason": str(e)})
                    continue
            lng, lat = geocode_cache[place]
            markers.append(
                {
                    "day": day["date"],
                    "color": color,
                    "place": place,
                    "node_id": stop.get("node_id"),
                    "type": stop.get("type"),
                    "lng": lng,
                    "lat": lat,
                }
            )

    return {
        "widget": "trip_map",
        "data": {"markers": markers, "days_legend": days_legend, "geocode_failures": geocode_failures},
    }


if __name__ == "__main__":
    import json

    demo_trip = trip_plan_module.new_trip_plan("schedule-widgets-demo-trip")

    day1 = trip_plan_module.get_or_create_day(demo_trip, "2026-10-01")
    trip_plan_module.add_stop(
        day1, "d1-n1", "attraction", "杭州西湖", arrival_transport="首站", arrival_time="09:00", end_time="11:00"
    )
    trip_plan_module.add_stop(
        day1,
        "d1-n2",
        "attraction",
        "杭州河坊街",
        arrival_transport="步行约15分钟",
        arrival_time="11:30",
        end_time="13:00",
        after_id="d1-n1",
    )

    day2 = trip_plan_module.get_or_create_day(demo_trip, "2026-10-02")
    trip_plan_module.add_stop(
        day2, "d2-n1", "attraction", "杭州灵隐寺", arrival_transport="首站", arrival_time="09:00", end_time="11:00"
    )

    print(json.dumps(build_trip_map_widget(demo_trip, city="杭州"), ensure_ascii=False, indent=2))
