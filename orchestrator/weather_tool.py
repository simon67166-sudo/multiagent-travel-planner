"""
和风天气工具 -- 灾害预警查询，给异常应变 Agent 判断"这个城市现在是不是真的有极端天气"用。
跟 map_tool.py 一样是通用基础设施，跟"编排"本身无关，谁都能 import。

依赖：pip install requests
运行前准备：在 orchestrator/.env 里加两行
  QWEATHER_KEY=你的和风天气 API KEY（控制台"项目管理"创建凭据时选 API KEY 类型，不是 JWT）
  QWEATHER_API_HOST=你的专属 API Host（同一个项目页面能看到，形如 xxxxxxxxxx.re.qweatherapi.com，
    不用带 https:// 前缀）——和风天气新账号是"一账号一专属域名"，公共的 devapi.qweather.com
    对新 key 直接 404，必须用这个专属域名。

免费个人开发者版：1000次/天。灾害预警（本模块用的接口）包含在免费的 8 个基础接口里，
辐射/海洋/热带气旋等付费专项接口没有，用不上。

坐标而不是地名查询：这个接口（/weatheralert/v1/current/{lat}/{lon}）直接按经纬度查，
不需要先转 LocationID，所以复用 map_tool.geocode() 拿坐标，不用再接一次和风天气自己的
GeoAPI（少一次网络调用，也少一个可能出错的环节）。
"""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv

import map_tool

load_dotenv(Path(__file__).parent / ".env")


def _get_key() -> str:
    key = os.environ.get("QWEATHER_KEY")
    if not key:
        raise RuntimeError("环境变量 QWEATHER_KEY 未设置，先去和风天气开放平台申请 API KEY 再用。")
    return key


def _base_url() -> str:
    host = os.environ.get("QWEATHER_API_HOST")
    if not host:
        raise RuntimeError(
            "环境变量 QWEATHER_API_HOST 未设置，去和风天气控制台“项目管理”找到这个 key 所在"
            "项目的专属 API Host（形如 xxxxxxxxxx.re.qweatherapi.com）再配置。"
        )
    return f"https://{host.rstrip('/')}"


def get_active_warnings(location: str, city: str | None = None) -> list[dict]:
    """
    查某个地名当前生效的灾害预警（暴雨/台风/大风等），返回列表，每条：
    {"headline", "event_type", "severity", "description", "effective_time", "expire_time"}
    severity 取值：unknown/minor/moderate/severe/extreme（越靠后越严重）。
    没有预警时返回空列表（这是正常情况，不是错误）。
    地名查不到坐标 / key 没配置会抛异常，调用方自己决定要不要兜底降级。

    city 透传给 map_tool.geocode() 做同名地点消歧（比如查"机场"这种小地名时更准），
    跟 map_tool.route_between() 的用法一致。
    """
    lng, lat = map_tool.geocode(location, city=city)
    resp = requests.get(
        f"{_base_url()}/weatheralert/v1/current/{lat}/{lng}",
        params={"key": _get_key()},
        timeout=10,
    )
    data = resp.json()
    if "alerts" not in data:
        raise ValueError(f"灾害预警查询失败: {location!r} -> {data}")
    return [
        {
            "headline": a.get("headline"),
            "event_type": (a.get("eventType") or {}).get("name"),
            "severity": a.get("severity"),
            "description": a.get("description"),
            "effective_time": a.get("effectiveTime"),
            "expire_time": a.get("expireTime"),
        }
        for a in data.get("alerts", [])
    ]


if __name__ == "__main__":
    import json

    print(json.dumps(get_active_warnings("香港"), ensure_ascii=False, indent=2))
