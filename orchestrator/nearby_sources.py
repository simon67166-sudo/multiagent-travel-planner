"""港澳附近游数据源 -- POI 搜索/周边地点/路线用高德，天气用和风天气逐小时预报
（weather_tool.py，2026-09-12 起统一天气来源，之前是接的 Open-Meteo，跟
exception_agent.check_weather() 用的灾害预警不是同一套数据源，两条链路徒增维护成本）。
高德/和风天气之外的地图数据源带缓存 + 限速，避免公共接口被打爆。"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from threading import RLock
import time
from urllib.parse import urlencode
import requests

_LOCK = RLock()
_CACHE = {}
_LAST = 0.0
CITY = {"澳门": {"bbox": (113.52,22.10,113.61,22.22)},
        "香港": {"bbox": (113.83,22.14,114.45,22.58)}}

def get_json(url, params=None, ttl=1800):
    global _LAST
    key=json.dumps([url,params],sort_keys=True,ensure_ascii=False)
    with _LOCK:
        cached=_CACHE.get(key)
        if cached and time.monotonic()-cached[0] < ttl:
            return deepcopy(cached[1])
        time.sleep(max(0,1.05-(time.monotonic()-_LAST)))
        _LAST=time.monotonic()
        try:
            response=requests.get(url,params=params,timeout=25,headers={"User-Agent":"TravelGuardian-Demo/0.2 (educational nearby itinerary)","Accept":"application/json"})
            response.raise_for_status()
            data=response.json()
        except (requests.RequestException,ValueError):
            raise RuntimeError("线上资料暂时无法取得，请稍后重试") from None
        if not isinstance(data,dict):
            raise RuntimeError("来源资料格式不符")
        data["_fetched_at"]=datetime.now(timezone.utc).isoformat()
        _CACHE[key]=(time.monotonic(),data)
        return deepcopy(data)

def in_city(city,lng,lat):
    west,south,east,north=CITY[city]["bbox"]
    return west <= lng <= east and south <= lat <= north

def distance(a,b):
    lat1,lat2=map(math.radians,(a["lat"],b["lat"]))
    dlat=lat2-lat1; dlon=math.radians(b["lng"]-a["lng"])
    return 6371000*2*math.asin(min(1,math.sqrt(math.sin(dlat/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2)))

def _amap(endpoint,params):
    import map_tool
    data=get_json("https://restapi.amap.com/v3/"+endpoint,{**params,"key":map_tool._get_key()})
    if data.get("status")!="1":
        raise RuntimeError("高德未返回可用结果，请确认 Key、权限及港澳覆盖范围")
    return data

def amap_poi(raw,stamp):
    lng,lat=map(float,raw["location"].split(","))
    return {"id":raw["id"],"name":raw["name"],"lng":lng,"lat":lat,"category":raw.get("type","地点"),
            "address":raw.get("address") or None,"opening_hours":None,"price":None,"crs":"GCJ02",
            "source":"高德 POI","source_url":"https://uri.amap.com/marker?"+urlencode({"position":raw["location"],"name":raw["name"]}),"fetched_at":stamp}

def find_origin(city,query):
    data=_amap("place/text",{"keywords":query,"city":city,"citylimit":"true","offset":5})
    options=[amap_poi(p,data["_fetched_at"]) for p in data.get("pois",[]) if p.get("location")]
    options=[p for p in options if in_city(city,p["lng"],p["lat"])]
    if not options: raise ValueError("找不到该城市内的位置，请输入更明确的地标或地址")
    return options[0]

def nearby(city,origin,radius=1500):
    data=_amap("place/around",{"location":f"{origin['lng']},{origin['lat']}","radius":radius,"types":"110000|050000|140100","sortrule":"distance","offset":25})
    return [amap_poi(p,data["_fetched_at"]) for p in data.get("pois",[]) if p.get("location") and p.get("id")!=origin["id"]]

def navigation_link(a,b,mode="walking"):
    return "https://www.google.com/maps/dir/?"+urlencode({"api":1,"origin":a["name"],"destination":b["name"],"travelmode":mode})

def route(a,b,mode="walking"):
    result={"available":False,"coordinates":[],"steps":[],"mode":mode,"crs":a["crs"],
            "navigation_url":navigation_link(a,b,mode),"transit_url":navigation_link(a,b,"transit")}
    if mode=="transit":
        result["steps"]=["开启公交方案，设定出发日期与时间。","查看上车站、行车方向、转乘站及下车站。","班次、票价、末班车以营运商公告为准；尚未取得即时公交方案。"]
        return result
    try:
        data=_amap("direction/"+mode,{"origin":f"{a['lng']},{a['lat']}","destination":f"{b['lng']},{b['lat']}"})
        paths=data.get("route",{}).get("paths",[])
        if not paths: return result
        path=paths[0]; coords=[]
        for step in path.get("steps",[]):
            coords.extend([list(map(float,c.split(","))) for c in step.get("polyline","").split(";") if c])
        steps=[s["instruction"] for s in path.get("steps",[]) if s.get("instruction")]
        source="https://lbs.amap.com/api/webservice/guide/api/direction"
        result.update(available=True,coordinates=coords,steps=steps,distance_m=round(float(path["distance"])),
                      duration_min=max(1,math.ceil(float(path["duration"])/60)),source_url=source,fetched_at=data["_fetched_at"])
    except RuntimeError:
        result["steps"]=["路线服务暂时不可用；请开启导航确认，未画出推测路线。"]
    return result

def summarize_weather(hours,start,end):
    start_key=start.strftime("%Y-%m-%dT%H:%M"); end_key=end.strftime("%Y-%m-%dT%H:%M")
    window=[h for h in hours if start_key<=h["time"]<end_key]
    if not window: return {"available":False,"reminders":["选定日期不在目前预报范围内（和风天气免费版最多查 10 天），请于出发前再查。"]}
    def values(key): return [h[key] for h in window if h.get(key) is not None]
    temps=values("temperature_c"); rain=values("rain_probability")
    thunder=any("雷" in (h.get("condition_text") or "") for h in window)
    reminders=[]
    if rain and max(rain)>=0.6: reminders.append("预报降雨机率较高，带雨具并预留室内备选；行程尚未自动更改。")
    if temps and max(temps)>=32: reminders.append("预报气温偏高，安排饮水及室内休息。")
    if thunder: reminders.append("预报时段可能有雷暴，出发前查阅官方警告，避免曝露的户外活动。")
    return {"available":True,"temperature_min":min(temps) if temps else None,"temperature_max":max(temps) if temps else None,
            "max_rain_probability":round(max(rain)*100) if rain else None,"reminders":reminders or ["预报可能更新，出发前请再次确认天气。"]}

def weather(city,origin,start,end):
    # 直接用 origin 的高德坐标查（GCJ02），和风天气接口对坐标系不敏感，不用再像以前
    # 接 Open-Meteo 时那样额外维护一份城市中心点 WGS84 坐标。
    import weather_tool
    hours_ahead=max(1,min(240,math.ceil((end-datetime.now()).total_seconds()/3600)+1))
    try:
        hours=weather_tool.get_hourly_forecast(origin["lng"],origin["lat"],hours=hours_ahead)
        result=summarize_weather(hours,start,end); result["fetched_at"]=datetime.now(timezone.utc).isoformat()
    except (RuntimeError,ValueError):
        result={"available":False,"reminders":["天气查询失败，目前没有可用预报。"]}
    result.update(source="和风天气逐小时预报",source_url="https://www.qweather.com/",forecast_for=start.isoformat(),
                  official_url="https://www.hko.gov.hk/tc/" if city=="香港" else "https://www.smg.gov.mo/")
    return result
