"""Small-demo online providers; cache results and throttle public requests."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
from threading import RLock
import time
from urllib.parse import urlencode
import requests

_LOCK = RLock()
_CACHE = {}
_LAST = 0.0
CITY = {"澳門": {"center": (113.5439,22.1987), "bbox": (113.52,22.10,113.61,22.22), "english": "Macau"},
        "香港": {"center": (114.17,22.30), "bbox": (113.83,22.14,114.45,22.58), "english": "Hong Kong"}}

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
            raise RuntimeError("線上資料暫時無法取得，請稍後重試") from None
        if not isinstance(data,dict):
            raise RuntimeError("來源資料格式不符")
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
        raise RuntimeError("高德未返回可用結果，請確認 Key、權限及港澳覆蓋範圍")
    return data

def amap_poi(raw,stamp):
    lng,lat=map(float,raw["location"].split(","))
    return {"id":raw["id"],"name":raw["name"],"lng":lng,"lat":lat,"category":raw.get("type","地點"),
            "address":raw.get("address") or None,"opening_hours":None,"price":None,"crs":"GCJ02",
            "source":"高德 POI","source_url":"https://uri.amap.com/marker?"+urlencode({"position":raw["location"],"name":raw["name"]}),"fetched_at":stamp}

def find_origin(city,query):
    if os.getenv("NEARBY_PROVIDER","osm")=="amap":
        data=_amap("place/text",{"keywords":query,"city":city,"citylimit":"true","offset":5})
        options=[amap_poi(p,data["_fetched_at"]) for p in data.get("pois",[]) if p.get("location")]
    else:
        aliases={"大三巴":"Ruins of St. Paul", "議事亭前地":"Senado Square", "尖沙咀":"Tsim Sha Tsui", "中環":"Central"}
        lng,lat=CITY[city]["center"]
        data=get_json("https://photon.komoot.io/api/",{"q":aliases.get(query,query)+" "+CITY[city]["english"],"lat":lat,"lon":lng,"limit":5},ttl=86400)
        options=[]
        for feature in data.get("features",[]):
            props=feature.get("properties",{}); coord=feature.get("geometry",{}).get("coordinates",[])
            if len(coord)!=2: continue
            typ={"N":"node","W":"way","R":"relation"}.get(props.get("osm_type"),"node")
            ident=f"{typ}/{props.get('osm_id')}"
            options.append({"id":ident,"name":props.get("name",query),"lng":coord[0],"lat":coord[1],"crs":"WGS84",
                            "source":"OpenStreetMap / Photon","source_url":"https://www.openstreetmap.org/"+ident,"fetched_at":data["_fetched_at"]})
    options=[p for p in options if in_city(city,p["lng"],p["lat"])]
    if not options: raise ValueError("找不到該城市內的位置，請輸入更明確的地標或地址")
    return options[0]

def nearby(city,origin,radius=1500):
    if origin["crs"]=="GCJ02":
        data=_amap("place/around",{"location":f"{origin['lng']},{origin['lat']}","radius":radius,"types":"110000|050000|140100","sortrule":"distance","offset":25})
        return [amap_poi(p,data["_fetched_at"]) for p in data.get("pois",[]) if p.get("location") and p.get("id")!=origin["id"]]
    point=f"around:{int(radius)},{origin['lat']},{origin['lng']}"
    query=f'[out:json][timeout:20];(nwr({point})["tourism"~"^(attraction|museum|viewpoint|gallery)$"]["name"];nwr({point})["leisure"="park"]["name"];nwr({point})["amenity"~"^(restaurant|cafe)$"]["name"];);out center 80;'
    data=get_json("https://overpass-api.de/api/interpreter",{"data":query})
    result=[]; seen=set()
    for raw in data.get("elements",[]):
        tag=raw.get("tags",{}); loc=raw.get("center",raw)
        if "lon" not in loc or "lat" not in loc: continue
        ident=f"{raw['type']}/{raw['id']}"; name=tag.get("name:zh-Hant") or tag.get("name:zh") or tag.get("name")
        if ident==origin["id"] or not name or name in seen: continue
        poi={"id":ident,"name":name,"lng":loc["lon"],"lat":loc["lat"],"crs":"WGS84",
             "category":tag.get("tourism") or tag.get("amenity") or tag.get("leisure"),
             "address":tag.get("addr:full") or tag.get("addr:street"),"opening_hours":tag.get("opening_hours"),
             "wheelchair":tag.get("wheelchair"),"cuisine":tag.get("cuisine"),"price":None,
             "source":"OpenStreetMap","source_url":"https://www.openstreetmap.org/"+ident,"fetched_at":data["_fetched_at"]}
        if not in_city(city,poi["lng"],poi["lat"]) or distance(origin,poi)>radius: continue
        seen.add(name); result.append(poi)
    return sorted(result,key=lambda p:distance(origin,p))[:30]

def navigation_link(a,b,mode="walking"):
    return "https://www.google.com/maps/dir/?"+urlencode({"api":1,"origin":a["name"],"destination":b["name"],"travelmode":mode})

def route(a,b,mode="walking"):
    result={"available":False,"coordinates":[],"steps":[],"mode":mode,"crs":a["crs"],
            "navigation_url":navigation_link(a,b,mode),"transit_url":navigation_link(a,b,"transit")}
    if mode=="transit":
        result["steps"]=["開啟公交方案，設定出發日期與時間。","查看上車站、行車方向、轉乘站及下車站。","班次、票價、末班車以營運商公告為準；尚未取得即時公交方案。"]
        return result
    try:
        if a["crs"]=="GCJ02":
            data=_amap("direction/"+mode,{"origin":f"{a['lng']},{a['lat']}","destination":f"{b['lng']},{b['lat']}"})
            paths=data.get("route",{}).get("paths",[])
            if not paths: return result
            path=paths[0]; coords=[]
            for step in path.get("steps",[]):
                coords.extend([list(map(float,c.split(","))) for c in step.get("polyline","").split(";") if c])
            steps=[s["instruction"] for s in path.get("steps",[]) if s.get("instruction")]
            source="https://lbs.amap.com/api/webservice/guide/api/direction"
        else:
            profile="routed-foot" if mode=="walking" else "routed-car"
            data=get_json(f"https://routing.openstreetmap.de/{profile}/route/v1/driving/{a['lng']},{a['lat']};{b['lng']},{b['lat']}",{"overview":"full","geometries":"geojson","steps":"true"},ttl=3600)
            if data.get("code")!="Ok" or not data.get("routes"): return result
            path=data["routes"][0]; coords=path["geometry"]["coordinates"]
            directions={"left":"左轉","right":"右轉","slight left":"靠左","slight right":"靠右","straight":"直行","sharp left":"向左急轉","sharp right":"向右急轉","uturn":"迴轉"}
            steps=[]
            for leg in path.get("legs",[]):
                for step in leg.get("steps",[]):
                    m=step.get("maneuver",{}); action="抵達" if m.get("type")=="arrive" else directions.get(m.get("modifier"),"沿路前進")
                    steps.append(f"{action} {step.get('name') or '道路'}，約 {round(step.get('distance',0))} 米")
            source="https://routing.openstreetmap.de/"
        result.update(available=True,coordinates=coords,steps=steps,distance_m=round(float(path["distance"])),
                      duration_min=max(1,math.ceil(float(path["duration"])/60)),source_url=source,fetched_at=data["_fetched_at"])
    except RuntimeError:
        result["steps"]=["路線服務暫時不可用；請開啟導航確認，未畫出推測路線。"]
    return result

def summarize_weather(raw,start,end):
    hourly=raw.get("hourly",{}); times=hourly.get("time",[])
    indices=[i for i,t in enumerate(times) if start.replace(minute=0)<=datetime.fromisoformat(t)<end]
    if not indices: return {"available":False,"reminders":["選定日期不在目前預報範圍內，請於出發前再查。"]}
    def values(key): return [hourly.get(key,[])[i] for i in indices if i<len(hourly.get(key,[])) and hourly[key][i] is not None]
    temps=values("temperature_2m"); rain=values("precipitation_probability"); codes=values("weather_code")
    reminders=[]
    if rain and max(rain)>=60: reminders.append("預報降雨機率較高，帶雨具並預留室內備選；行程尚未自動更改。")
    if temps and max(temps)>=32: reminders.append("預报氣溫偏高，安排飲水及室內休息。")
    if any(c>=95 for c in codes): reminders.append("預報時段可能有雷暴，出發前查閱官方警告，避免曝露的戶外活動。")
    return {"available":True,"temperature_min":min(temps) if temps else None,"temperature_max":max(temps) if temps else None,
            "max_rain_probability":max(rain) if rain else None,"reminders":reminders or ["預報可能更新，出發前請再次確認天氣。"]}

def weather(city,origin,start,end):
    lng,lat=(origin["lng"],origin["lat"]) if origin.get("crs")=="WGS84" else CITY[city]["center"]
    try:
        raw=get_json("https://api.open-meteo.com/v1/forecast",{"longitude":lng,"latitude":lat,"hourly":"temperature_2m,precipitation_probability,weather_code","forecast_days":16,"timezone":"Asia/Macau"},ttl=600)
        result=summarize_weather(raw,start,end); result["fetched_at"]=raw["_fetched_at"]
    except RuntimeError:
        result={"available":False,"reminders":["天氣查詢失敗，目前沒有可用預報。"]}
    result.update(source="Open-Meteo 數值預報（非官方警告）",source_url="https://open-meteo.com/",forecast_for=start.isoformat(),
                  official_url="https://www.hko.gov.hk/tc/" if city=="香港" else "https://www.smg.gov.mo/")
    return result
