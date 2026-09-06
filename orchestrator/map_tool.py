"""
高德地图工具 -- 地理编码 + 路径规划，给行程/路线 Agent 算真实的距离/耗时/路线用。
跟 llm_tool.py 一样是通用基础设施，跟"编排"本身无关，谁都能 import。

依赖：pip install requests
运行前准备：在 orchestrator/.env 里加一行 AMAP_KEY=你的高德 Web服务 API Key
（注意高德的 key 分"Web服务"和"Web端(JS API)"两种，这里用的是 Web服务 key——
去 https://lbs.amap.com 控制台"创建应用"时要选 Web服务 类型，不是 JS API）

免费额度：个人开发者未认证 6000次/天，QPS 100；实名认证后 30万次/月，QPS 200，
demo/比赛阶段完全够用。
"""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

_BASE_URL = "https://restapi.amap.com/v3"


def _get_key() -> str:
    key = os.environ.get("AMAP_KEY")
    if not key:
        raise RuntimeError("环境变量 AMAP_KEY 未设置，先去高德开放平台申请 Web服务 API Key 再用。")
    return key


def geocode(address: str, city: str | None = None) -> tuple[float, float]:
    """地名/地址转经纬度，返回 (lng, lat)。查不到会抛 ValueError。"""
    params = {"address": address, "key": _get_key()}
    if city:
        params["city"] = city
    resp = requests.get(f"{_BASE_URL}/geocode/geo", params=params, timeout=10)
    data = resp.json()
    if data.get("status") != "1" or not data.get("geocodes"):
        raise ValueError(f"地理编码失败: {address!r} -> {data}")
    lng_str, lat_str = data["geocodes"][0]["location"].split(",")
    return float(lng_str), float(lat_str)


def _direction(mode: str, origin: tuple[float, float], destination: tuple[float, float]) -> dict:
    endpoint = {"walking": "walking", "driving": "driving"}[mode]
    params = {
        "origin": f"{origin[0]},{origin[1]}",
        "destination": f"{destination[0]},{destination[1]}",
        "key": _get_key(),
    }
    resp = requests.get(f"{_BASE_URL}/direction/{endpoint}", params=params, timeout=10)
    data = resp.json()
    if data.get("status") != "1" or not data.get("route", {}).get("paths"):
        raise ValueError(f"路径规划失败: {data}")
    path = data["route"]["paths"][0]
    return {
        "distance_m": int(path["distance"]),
        "duration_min": round(int(path["duration"]) / 60),
        "polyline": [step["polyline"] for step in path.get("steps", [])],
    }


def route_between(place_a: str, place_b: str, mode: str = "walking", city: str | None = None) -> dict:
    """
    算两个地点之间的距离/耗时/路线。mode: "walking"（步行）| "driving"（驾车）。
    city 是可选的城市提示，同名地点在不同城市容易查错，传了更准。
    这是 route_agent.py 主要会调的函数。
    """
    origin = geocode(place_a, city=city)
    destination = geocode(place_b, city=city)
    result = _direction(mode, origin, destination)
    result["origin"] = {"place": place_a, "lng": origin[0], "lat": origin[1]}
    result["destination"] = {"place": place_b, "lng": destination[0], "lat": destination[1]}
    result["mode"] = mode
    return result


if __name__ == "__main__":
    import json

    print(json.dumps(route_between("杭州西湖", "杭州灵隐寺", mode="driving"), ensure_ascii=False, indent=2))
