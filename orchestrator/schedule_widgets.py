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

import math
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

# 地图分框用的两个距离阈值：
# - 景点/餐饮/酒店这些"行程点"只要彼此距离小于这个值，就算同一个框（同一次出行范围内），
#   超过就拆到不同的框——不然选了机票之后，两个城市的经纬度差几百公里，前端 setFitView()
#   会把整个地图缩到看不清任何一个景点的程度，这是接真实数据测试时发现的真问题。
#   50km 是"同城/同一日游范围"和"跨城市"之间一个粗略但够用的分界，不是精确算出来的。
_FRAME_CLUSTER_THRESHOLD_M = 50_000
# - 机场不参与上面的聚类（出发/到达机场天然可能在两个不同城市，不该左右"应该分几个框"这个
#   判断），聚完框之后再看每个机场离哪个框最近；在这个距离内就贴到那个框上（比如到达机场
#   通常就在目的地城市附近），超过这个距离就不勉强塞进任何框，单独列出来，不参与地图缩放计算。
_AIRPORT_ATTACH_THRESHOLD_M = 80_000


def _color_for_day(day_index: int) -> str:
    return _DAY_COLOR_PALETTE[day_index % len(_DAY_COLOR_PALETTE)]


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """两点间大圆距离，米。a/b 是 (lng, lat) 元组。"""
    lng1, lat1 = math.radians(a[0]), math.radians(a[1])
    lng2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


def _cluster_by_distance(markers: list[dict], threshold_m: float) -> list[list[dict]]:
    """
    贪心单链接聚类：一个点只要跟某个簇里任意一个成员的距离 <= threshold，就并入那个簇。
    简单、不追求全局最优（有链式效应的理论风险——A-B近、B-C近但A-C远，也会被并成一簇），
    但城市之间通常要么很近（同城内，几十公里内）要么很远（上百公里），实际数据里够用。
    """
    clusters: list[list[dict]] = []
    for marker in markers:
        coord = (marker["lng"], marker["lat"])
        matched = next(
            (c for c in clusters if any(_haversine_m(coord, (m["lng"], m["lat"])) <= threshold_m for m in c)),
            None,
        )
        if matched is not None:
            matched.append(marker)
        else:
            clusters.append([marker])
    return clusters


def _cluster_center(cluster: list[dict]) -> dict:
    return {
        "lng": sum(m["lng"] for m in cluster) / len(cluster),
        "lat": sum(m["lat"] for m in cluster) / len(cluster),
    }


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
    左侧日程面板的地图组件，按地理距离拆成多个"框"（frames），不是一张塞满所有点的大地图：
    选了跨城市的机票之后，出发/到达机场经纬度差几百公里，如果硬塞进同一张地图，前端
    setFitView() 会把地图缩到连本地景点都看不清的程度——这是接真实数据测试时发现的真问题。

    分框逻辑：
    - 景点/餐饮/酒店这些"行程点"先按距离聚类（见 _cluster_by_distance），彼此在
      _FRAME_CLUSTER_THRESHOLD_M（50km）以内的算同一框，这样"多城市同游"会自然按城市分开，
      不需要额外记录"这个点属于哪个城市"。
    - 机票起降机场**不参与聚类**（不该由机场决定"应该分几个框"——出发机场常常在完全无关的
      另一个城市）。聚完框之后再看每个机场离哪个框最近，在 _AIRPORT_ATTACH_THRESHOLD_M
      （80km）以内就贴上去（比如到达机场通常就在目的地城市附近），够不着任何框就放进
      unclustered_airports，不参与任何一个框的缩放计算，前端可以用文字/小图标单独展示。
    - 同一天的行程节点依旧用同一个颜色（按"第几天"分配），颜色跟 build_day_timeline_widget
      对齐；只是现在同一个颜色的点可能分布在不同的框里（比如第2天在杭州、第4天飞去了北京）。

    trip_plan_obj: shared_state["trip_plan"]（trip_plan.py 的原始结构，不是 render() 之后的）。
    city: 地理编码/路线查询用的城市提示，同名地点在不同城市容易查错，传了更准；机场查询
    不受这个参数影响（原因见下面机场那段注释）。

    返回：
    {
      "widget": "trip_map",
      "data": {
        "frames": [{"frame_id":, "center": {"lng":,"lat":}, "markers": [...], "routes": [...]}],
        "unclustered_airports": [{day: None, color, place, type: "airport", role, flight_no, status, lng, lat}],
        "days_legend": [{"date": 日期, "color": 颜色}],
        "geocode_failures": [{"place":, "reason":}],
        "route_failures": [{"day":, "from":, "to":, "reason":}],
      }
    }
    """
    rendered = trip_plan_module.render(trip_plan_obj)
    days_legend = []
    geocode_failures = []
    route_failures = []
    geocode_cache: dict[tuple[str, str | None], tuple[float, float]] = {}

    def geocode_or_record_failure(place: str, *, geocode_city: str | None = city) -> tuple[float, float] | None:
        cache_key = (place, geocode_city)
        if cache_key in geocode_cache:
            return geocode_cache[cache_key]
        try:
            geocode_cache[cache_key] = map_tool.geocode(place, city=geocode_city)
        except Exception as e:
            geocode_failures.append({"place": place, "reason": str(e)})
            return None
        return geocode_cache[cache_key]

    # --- 第一步：收集可聚类的点（景点/餐饮/酒店）+ 它们之间的连线，先不管分几个框 ---
    clusterable_markers: list[dict] = []
    pending_routes: list[dict] = []  # 先记 from/to 坐标，聚完框再决定归到哪个框

    for day_index, day in enumerate(rendered["days"]):
        color = _color_for_day(day_index)
        days_legend.append({"date": day["date"], "color": color})

        stops = day["stops"]
        prev_place = None
        prev_coord = None
        for stop in stops:
            place = stop.get("place")
            if not place:
                continue
            coord = geocode_or_record_failure(place)
            if coord is not None:
                lng, lat = coord
                clusterable_markers.append(
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
                    pending_routes.append(
                        {"day": day["date"], "color": color, "coordinates": coords, "anchor": prev_coord or coord}
                    )
                except Exception as e:
                    route_failures.append({"day": day["date"], "from": prev_place, "to": place, "reason": str(e)})
            prev_place, prev_coord = place, coord

    # --- 酒店：优先用地址，没有地址就退化成用名字查；也算进可聚类候选 ---
    for hotel in rendered["hotels"]:
        place = hotel.get("address") or hotel.get("name")
        if not place:
            continue
        coord = geocode_or_record_failure(place)
        if coord is None:
            continue
        lng, lat = coord
        clusterable_markers.append(
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

    # --- 第二步：按距离聚类成若干个框 ---
    clusters = _cluster_by_distance(clusterable_markers, _FRAME_CLUSTER_THRESHOLD_M)
    frames = [
        {"frame_id": f"frame-{i + 1}", "center": _cluster_center(cluster), "markers": cluster, "routes": []}
        for i, cluster in enumerate(clusters)
    ]

    # 把连线分配到框：用连线终点所在的框（起点通常也在同一个框，真跨框的极端情况——比如
    # 同一天的行程真的横跨两个 50km 以外的地点——就按终点框处理，不强求完美，不丢线）
    for route in pending_routes:
        anchor = route.pop("anchor")
        target_frame = min(
            frames,
            key=lambda f: min(_haversine_m(anchor, (m["lng"], m["lat"])) for m in f["markers"]),
            default=None,
        )
        if target_frame is not None:
            target_frame["routes"].append(route)

    # --- 第三步：机票起降机场，不参与聚类，聚完框再看离哪个框最近能不能贴上去 ---
    # 注意：这里不传 city 限定——出发/到达机场天然分属两个不同城市，用行程主城市限定
    # geocode 反而会把出发机场错误匹配成"主城市里名字最像的机场"（比如查"上海虹桥"却限定
    # city="杭州"，会被高德强行匹配成杭州萧山机场附近的结果），这个坑是接真实 key 测试时踩出来的。
    unclustered_airports = []
    for flight in rendered["flights"]:
        for role, place in (("depart", flight.get("from_")), ("arrive", flight.get("to"))):
            if not place:
                continue
            coord = geocode_or_record_failure(place, geocode_city=None)
            if coord is None:
                continue
            marker = {
                "day": None,
                "color": _AIRPORT_COLOR,
                "place": place,
                "type": "airport",
                "role": role,
                "flight_no": flight.get("flight_no"),
                "status": flight.get("status"),
                "lng": coord[0],
                "lat": coord[1],
            }
            nearest_frame = min(
                frames,
                key=lambda f: _haversine_m(coord, (f["center"]["lng"], f["center"]["lat"])),
                default=None,
            )
            if nearest_frame is not None and _haversine_m(coord, (nearest_frame["center"]["lng"], nearest_frame["center"]["lat"])) <= _AIRPORT_ATTACH_THRESHOLD_M:
                nearest_frame["markers"].append(marker)
            else:
                unclustered_airports.append(marker)

    return {
        "widget": "trip_map",
        "data": {
            "frames": frames,
            "unclustered_airports": unclustered_airports,
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
