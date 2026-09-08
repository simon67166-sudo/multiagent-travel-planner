"""
左侧行程面板展示组件 -- 服务对象是左侧实时日程面板（server.py 的 GET /trip，
web/index.html 目前的文字列表），跟右侧聊天框的 widgets.py 是同一个"数据/展示分离"思路：
这里只算"给前端画图/排版用的数据"，不产出任何 HTML/画布逻辑，真正怎么画留给前端。

三个 widget：
- trip_map：地图标点 + 路线连线（景点/餐饮按天上色，机票/酒店用固定颜色单独标出来）
- day_timeline：每天的行程按时间顺序竖排，按天分模块，模块颜色跟 trip_map 的当天颜色对齐
- booking_panel：trip_plan 里已经落地确认的机票/酒店记录（不是候选推荐，候选推荐见 widgets.py
  的 build_flight_picker_widget/build_hotel_picker_widget）

设计原则：
- 数据来源必须是 trip_plan 里真实存在的记录，不允许编造；地点坐标/真实路线用 map_tool 查，
  查不到就跳过那一条并记进 *_failures，不让一个查询失败拖垮整个 widget。
- 同一天的行程节点（景点/餐饮）用同一个颜色，颜色按"这是第几天"分配，不是按地点类型分配。
- 机票/酒店不属于"某一天"（酒店跨好几晚、机票是独立于 days 结构的顶层记录），地图上用固定颜色
  的图标区分，不占用 _DAY_COLOR_PALETTE。
- flights 的 from_/to、hotels 的 address 建议存能被地理编码识别的地名/地址（比如"杭州萧山国际机场"），
  不建议只存三字码（比如"HGH"）——高德地理编码认不出机场三字码，会直接进 geocode_failures。

模型档位：不需要 LLM——纯确定性计算（按天/类型分配颜色 + 查真实地理数据）。
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

_AIRPORT_COLOR = "#33414f"  # 机票标记固定用深灰蓝，跟按天变化的景点颜色区分开
_HOTEL_COLOR = "#8a6d3b"  # 酒店标记固定用棕色

# 跟 agents/route_agent.py 里的同名阈值保持一致（直线距离超过这个就从步行改算驾车路线），
# 这里独立复制一份常量而不是 import route_agent，避免"展示层"反过来依赖具体某个 Agent 的实现。
_WALK_DRIVE_THRESHOLD_M = 2000


def _color_for_day(day_index: int) -> str:
    return _DAY_COLOR_PALETTE[day_index % len(_DAY_COLOR_PALETTE)]


def _parse_polyline_segments(segments: list[str]) -> list[list[float]]:
    """
    把 map_tool.route_between() 返回的 polyline（每段路 "lng,lat;lng,lat;..." 的字符串列表）
    展平成一条连续的 [[lng, lat], ...] 坐标序列，前端直接拿去画线。
    """
    coords = []
    for segment in segments:
        for point in segment.split(";"):
            if not point:
                continue
            lng_str, lat_str = point.split(",")
            coords.append([float(lng_str), float(lat_str)])
    return coords


def _route_leg_coords(place_a: str, place_b: str, city: str | None) -> list[list[float]]:
    """查 place_a -> place_b 的真实路线（超过阈值自动从步行切驾车），返回展平的坐标序列。"""
    info = map_tool.route_between(place_a, place_b, mode="walking", city=city)
    if info["distance_m"] > _WALK_DRIVE_THRESHOLD_M:
        info = map_tool.route_between(place_a, place_b, mode="driving", city=city)
    return _parse_polyline_segments(info["polyline"])


def build_trip_map_widget(trip_plan_obj: dict, city: str | None = None) -> dict:
    """
    左侧日程面板的地图组件：
    - markers：景点/餐饮节点（按天上色）+ 机票起降机场（固定深灰蓝）+ 酒店（固定棕色）
    - routes：每天把当天的节点依次连成一条真实路线（查真实路况，不是直线连线），颜色跟当天 markers 一致

    trip_plan_obj: shared_state["trip_plan"]（trip_plan.py 的原始结构，不是 render() 之后的）。
    city: 地理编码/路线查询用的城市提示，同名地点在不同城市容易查错，传了更准。

    返回：
    {
      "widget": "trip_map",
      "data": {
        "markers": [{day|None, color, place, node_id, type, lng, lat, ...}],
        "routes": [{"day": 日期, "color": 颜色, "coordinates": [[lng,lat], ...]}],
        "days_legend": [{"date": 日期, "color": 颜色}],
        "geocode_failures": [{"place":, "reason":}],
        "route_failures": [{"day":, "from":, "to":, "reason":}],
      }
    }
    """
    rendered = trip_plan_module.render(trip_plan_obj)
    markers = []
    routes = []
    days_legend = []
    geocode_failures = []
    route_failures = []
    geocode_cache: dict[str, tuple[float, float]] = {}

    def geocode_or_record_failure(place: str) -> tuple[float, float] | None:
        if place in geocode_cache:
            return geocode_cache[place]
        try:
            geocode_cache[place] = map_tool.geocode(place, city=city)
        except Exception as e:
            geocode_failures.append({"place": place, "reason": str(e)})
            return None
        return geocode_cache[place]

    # --- 每天的景点/餐饮：标点 + 连线 ---
    for day_index, day in enumerate(rendered["days"]):
        color = _color_for_day(day_index)
        days_legend.append({"date": day["date"], "color": color})

        stops = day["stops"]
        prev_place = None
        for stop in stops:
            place = stop.get("place")
            if not place:
                continue
            coord = geocode_or_record_failure(place)
            if coord is not None:
                lng, lat = coord
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
            if prev_place is not None:
                try:
                    coords = _route_leg_coords(prev_place, place, city)
                    routes.append({"day": day["date"], "color": color, "coordinates": coords})
                except Exception as e:
                    route_failures.append({"day": day["date"], "from": prev_place, "to": place, "reason": str(e)})
            prev_place = place

    # --- 机票：起飞/降落机场各标一个点，不参与按天上色 ---
    for flight in rendered["flights"]:
        for role, place in (("depart", flight.get("from_")), ("arrive", flight.get("to"))):
            if not place:
                continue
            coord = geocode_or_record_failure(place)
            if coord is None:
                continue
            lng, lat = coord
            markers.append(
                {
                    "day": None,
                    "color": _AIRPORT_COLOR,
                    "place": place,
                    "type": "airport",
                    "role": role,
                    "flight_no": flight.get("flight_no"),
                    "status": flight.get("status"),
                    "lng": lng,
                    "lat": lat,
                }
            )

    # --- 酒店：优先用地址，没有地址就退化成用名字查 ---
    for hotel in rendered["hotels"]:
        place = hotel.get("address") or hotel.get("name")
        if not place:
            continue
        coord = geocode_or_record_failure(place)
        if coord is None:
            continue
        lng, lat = coord
        markers.append(
            {
                "day": None,
                "color": _HOTEL_COLOR,
                "place": place,
                "type": "hotel",
                "name": hotel.get("name"),
                "check_in": hotel.get("check_in"),
                "check_out": hotel.get("check_out"),
                "lng": lng,
                "lat": lat,
            }
        )

    return {
        "widget": "trip_map",
        "data": {
            "markers": markers,
            "routes": routes,
            "days_legend": days_legend,
            "geocode_failures": geocode_failures,
            "route_failures": route_failures,
        },
    }


def build_day_timeline_widget(trip_plan_obj: dict) -> dict:
    """
    左侧"每日行程"模块：把每天的行程节点按时间顺序整理成竖排列表，按天分模块。
    每个模块自带的颜色跟 build_trip_map_widget() 里同一天的颜色对齐，方便地图和列表对照着看。
    """
    rendered = trip_plan_module.render(trip_plan_obj)
    days = []
    for day_index, day in enumerate(rendered["days"]):
        stops = [
            {
                "time_range": f"{stop.get('arrival_time', '待定')}–{stop.get('end_time', '待定')}",
                "place": stop.get("place"),
                "type": stop.get("type"),
                "meal_type": stop.get("meal_type"),
                "arrival_transport": stop.get("arrival_transport"),
                "node_id": stop.get("node_id"),
            }
            for stop in day["stops"]
        ]
        days.append({"date": day["date"], "color": _color_for_day(day_index), "stops": stops})
    return {"widget": "day_timeline", "data": {"days": days}}


def build_booking_panel_widget(trip_plan_obj: dict) -> dict:
    """
    左侧"机票/酒店"模块：原样呈现 trip_plan 里已经落地确认的 flights/hotels 记录。
    注意这跟 widgets.py 的 build_flight_picker_widget/build_hotel_picker_widget 不是一回事——
    那两个是"聊天框里给用户挑的候选推荐"，这里是"已经确认下来、要显示在左侧行程里的记录"。
    """
    rendered = trip_plan_module.render(trip_plan_obj)
    return {"widget": "booking_panel", "data": {"flights": rendered["flights"], "hotels": rendered["hotels"]}}


if __name__ == "__main__":
    import json

    demo_trip = trip_plan_module.new_trip_plan("schedule-widgets-demo-trip")

    trip_plan_module.add_flight(
        demo_trip,
        {
            "flight_no": "MU5137",
            "from_": "上海虹桥国际机场",
            "to": "杭州萧山国际机场",
            "depart_time": "08:00",
            "arrive_time": "09:10",
            "status": "on_time",
        },
    )
    trip_plan_module.add_hotel(
        demo_trip,
        {
            "name": "西湖国宾馆",
            "address": "杭州西湖区杨公堤18号",
            "check_in": "2026-10-01",
            "check_out": "2026-10-03",
            "price": 1280,
            "cancel_policy": "24小时内免费取消",
        },
    )

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
    print(json.dumps(build_day_timeline_widget(demo_trip), ensure_ascii=False, indent=2))
    print(json.dumps(build_booking_panel_widget(demo_trip), ensure_ascii=False, indent=2))
