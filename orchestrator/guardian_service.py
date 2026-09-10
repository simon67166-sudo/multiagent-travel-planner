"""Application workflow: retrieve evidence, dispatch skills, verify, propose.

No function here commits a trip. HTTP sends drafts to GuardianStore and only the
owner's version-checked acceptance commits. Models cannot relax hard constraints.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import json, math, os
import trip_plan
from guardian_store import canonical_itinerary


def number(value):
    return float(value) if type(value) in (int,float) and math.isfinite(value) and value>=0 else None

def stops_of(itinerary):
    nearby=itinerary.get("nearby_plan")
    if nearby is not None: return deepcopy(nearby.get("stops",[]))
    return [dict(node.get("poi",{}),name=node.get("place"),arrival_time=node.get("arrival_time"),end_time=node.get("end_time")) for day in itinerary.get("trip_plan",{}).get("days",{}).values() for node in trip_plan.day_stops(day)]

def price_of(poi):
    price=poi.get("price")
    if isinstance(price,dict): return number(price.get("amount")),price.get("currency")
    return number(price),poi.get("currency")

def validate_itinerary(itinerary,members):
    from guardian_skills import validate_itinerary_feasibility
    check=validate_itinerary_feasibility(itinerary,members)
    previous=None
    for poi in stops_of(itinerary):
        try:
            arrival=datetime.strptime(poi.get("arrival_time"),"%H:%M")
            end=datetime.strptime(poi.get("end_time"),"%H:%M")
            if end<arrival or (previous is not None and arrival<previous): check["violations"].append("行程時間重疊或結束早於開始")
            previous=end
        except (TypeError,ValueError): check["unknowns"].append("交通或停留時刻待確認")
    if check["violations"]: check.update(status="infeasible",feasible=False)
    return check


def supervise(message,team):
    """Pro chooses allowlisted skills; it never supplies constraints or venue facts."""
    import llm_tool
    from guardian_skills import SKILL_IDS, evaluate_candidates
    summary=evaluate_candidates([],team["members"])["constraint_summary"]
    try:
        raw=llm_tool.call_llm([{"role":"system","content":"你是旅行總調度。根據文字及限制摘要選擇多個技能，不得修改限制。只輸出 JSON {skills:[技能ID],location:起點地標或null}。可選："+", ".join(SKILL_IDS)},
            {"role":"user","content":json.dumps({"message":message,"constraints":summary,"city":team["itinerary"]["city"]},ensure_ascii=False)}])
        clean=raw.strip()
        if clean.startswith("```"): clean="\n".join(clean.splitlines()[1:-1])
        parsed=json.loads(clean)
        selected=parsed.get("skills")
        if not isinstance(selected,list): raise ValueError("invalid skills")
        selected=[x for x in selected if isinstance(x,str) and x in SKILL_IDS]
        location=parsed.get("location")
        return {"skills":list(dict.fromkeys(selected)) or None,"location":location if isinstance(location,str) and 0<len(location)<=200 else None,"source":"Pro"}
    except Exception:
        return {"skills":None,"location":None,"source":"deterministic fallback"}


def _input(data):
    if not isinstance(data,dict) or not isinstance(data.get("message"),str) or not 1<=len(data["message"].strip())<=8000: raise ValueError("請提供 1–8000 字的 message")
    if data.get("mode","real") not in ("real","demo"): raise ValueError("mode 必須為 real 或 demo")
    if data.get("city","澳門") not in ("澳門","香港"): raise ValueError("目前僅支援澳門與香港")
    if "skills" in data and (not isinstance(data["skills"],list) or not all(isinstance(x,str) for x in data["skills"])): raise ValueError("skills 必須是文字清單")

def build_context(data,team):
    from guardian_demo import load_demo
    import guardian_sources as sources
    mode=data.get("mode","real"); city=data.get("city",team["itinerary"]["city"])
    itinerary=deepcopy(team["itinerary"])
    if mode=="demo":
        context=load_demo(city)
        context["members"]=deepcopy(team["members"])
        if stops_of(itinerary): context["itinerary"]=itinerary
        else:
            for key in ("trip_id","hotels","flights","weather_alerts"):
                context["itinerary"]["trip_plan"][key]=deepcopy(itinerary["trip_plan"].get(key,[]))
        context["mode"]="demo"
        return context
    context={"city":city,"mode":"real","members":deepcopy(team["members"]),"itinerary":itinerary,"pois":[],"content":[],"events":[],"evidence":[],"errors":[]}
    nearby=itinerary.get("nearby_plan") or {}
    origin=nearby.get("origin") if nearby.get("request",{}).get("city")==city else None
    location=data.get("location") or origin
    if isinstance(location,str):
        try: origin=sources.nearby_sources.find_origin(city,location)
        except (ValueError,RuntimeError): context["errors"].append({"source":"origin","message":"未找到起點；路線待確認"})
    keywords={"toilet":"公共廁所","food_risk":"餐廳","souvenir":"手信","diy":"手作","museum":"博物館","hidden_menu":"咖啡"}
    selected=data.get("skills") or []
    keyword="|".join(dict.fromkeys(keywords[k] for k in selected if k in keywords))
    location={**origin,"keywords":keyword} if origin else {"keywords":keyword} if keyword else location
    found=sources.fetch_context(city,location)
    context.update(pois=found.get("pois",[]),weather=found.get("weather",{}),evidence=found.get("evidence",[]),errors=found.get("errors",[]))
    raw_weather=context["weather"]
    if raw_weather.get("forecast",{}).get("available"):
        chosen=deepcopy(raw_weather["forecast"])
        target=nearby.get("request",{}).get("date",datetime.now().date().isoformat())
        for row in chosen.get("records",[]): row["casts"]=[c for c in row.get("casts",[]) if c.get("date")==target]
        chosen["available"]=any(row.get("casts") for row in chosen.get("records",[]))
        context["weather"]=chosen
    elif raw_weather.get("live",{}).get("available"): context["weather"]=raw_weather["live"]
    if origin:
        context["origin"]=origin
        def distance_to(poi):
            route=sources.route(origin,poi,city,"walking")
            poi["route"]=route
            if route.get("available") and number(route.get("distance_m")) is not None: poi["walking_distance_m"]=route["distance_m"]
            return poi
        # Bound network work, not team size. Every member still participates in ranking.
        with ThreadPoolExecutor(max_workers=3) as pool:
            context["pois"]=list(pool.map(distance_to,context["pois"][:8]))
    content_path=os.getenv("GUARDIAN_CONTENT_PATH")
    if content_path:
        try: context["content"]=sources.load_content(content_path)
        except ValueError: context["errors"].append({"source":"local_content","message":"本地內容格式無效，未使用"})
    return context

def attach_routes(result,context):
    """Attach actual provider legs to navigation skills; never synthesize geometry."""
    import guardian_sources as sources
    origin=context.get("origin")
    for row in result.get("skill_results",[]):
        if row["skill"] not in ("toilet","citywalk","museum","photo","diy"): continue
        routes=[]; current=origin
        for poi in row.get("candidates",[])[:6]:
            if context["mode"]=="real" and current and all(k in poi for k in ("lng","lat")):
                leg=sources.route(current,poi,context["city"],"walking")
                leg.update(from_name=current.get("name"),to_name=poi.get("name"))
                routes.append(leg)
                if row["skill"]=="citywalk": current=poi
            else: routes.append({"available":False,"from_name":current.get("name") if current else None,"to_name":poi.get("name"),"coordinates":[],"reason":"缺少起點或真實地點座標；演示不提供假路線"})
        row["routes"]=routes


def execute(data,team):
    from guardian_skills import run_skills
    _input(data)
    routing=supervise(data["message"],team) if data.get("mode","real")=="real" and not data.get("skills") else {"skills":data.get("skills"),"source":"explicit or demo"}
    data={**data,"location":data.get("location") or routing.get("location"),"skills":data.get("skills") or routing.get("skills")}
    context=build_context(data,team)
    result=run_skills(data["message"],context,routing.get("skills"))
    result["supervisor"]={"model":routing["source"],"selected_skills":[r["skill"] for r in result["skill_results"]]}
    result.setdefault("sources",[])
    result.setdefault("skill_results",[])
    result["mode"]=context["mode"]
    attach_routes(result,context)
    result["providers"]={"xhs":"unconfigured","flyai":"unconfigured","hotel":"unconfigured","flight":"unconfigured"}
    result["errors"]=context.get("errors",[])
    # An initial demo itinerary is itself a proposal, never installed implicitly.
    draft=result.get("proposed_itinerary")
    if not draft and context["mode"]=="demo" and not stops_of(team["itinerary"]): draft=deepcopy(context.get("itinerary"))
    if draft:
        draft["mode"]=context["mode"]
        check=validate_itinerary(draft,team["members"])
        result["validation"]=check
        if check["violations"]:
            result["proposed_itinerary"]=None
            result["conflicts"]=result.get("conflicts",[])+check["violations"]
            result["chat_reply"]+="\n無法提交此方案："+"；".join(check["violations"])
        else:
            result["proposed_itinerary"]=draft
            if check["unknowns"]: result["chat_reply"]+="\n待確認："+"；".join(check["unknowns"])
    return result

def chat(message,state):
    from guardian_skills import run_skills
    import nearby_planner, llm_tool, widgets
    from agents import ota_hotel_agent
    team=state["_guardian"]
    mode="demo" if any(x in message.lower() for x in ("演示","模擬","模拟","demo")) else "real"
    city="香港" if "香港" in message else "澳門" if any(x in message for x in ("澳門","澳门")) else team["itinerary"]["city"]
    data={"message":message,"mode":mode,"city":city}
    output_widgets=[]
    if mode=="real" and any(k in message.lower() for k in ("附近","攻略","行程","路線","路线","計劃","计划","規劃","规划")):
        working=deepcopy(state)
        nearby_output,_=nearby_planner.from_chat(message,working)
        if not nearby_output.get("nearby_plan"):
            result={**nearby_output,"skill_results":[],"sources":[]}
        else:
            plan=nearby_output["nearby_plan"]
            context={"city":city,"mode":"real","members":team["members"],"itinerary":canonical_itinerary(working),"pois":plan["stops"],"weather":plan.get("weather",{}),"events":[],"content":[],"origin":plan["origin"]}
            routing=supervise(message,team)
            result=run_skills(message,context,routing.get("skills"))
            result["supervisor"]={"model":routing["source"],"selected_skills":[r["skill"] for r in result["skill_results"]]}
            draft=result.get("proposed_itinerary") or context["itinerary"]
            check=validate_itinerary(draft,team["members"])
            result["validation"]=check
            result["proposed_itinerary"]=None if check["violations"] else draft
            result["chat_reply"]=nearby_output["chat_reply"]+"\n"+result["chat_reply"]
            result["sources"]+=plan.get("evidence",[])
    else: result=execute(data,team)
    if any(k in message.lower() for k in ("酒店","機票","机票","hotel","flight")):
        if mode=="demo":
            candidates=ota_hotel_agent.run(state,location=city)["candidates"]
            for item in candidates:
                item.update(data_kind="demo",booking_status="not_booked")
            for factory in (widgets.build_flight_picker_widget,widgets.build_hotel_picker_widget):
                widget=factory(candidates)
                if widget: output_widgets.append(widget)
            result["chat_reply"]+="\n酒店／機票卡片為演示資料，選擇只會加入提案，沒有實際預訂。"
        else: result["chat_reply"]+="\n酒店／機票供應商尚未接通；不提供即時庫存或假預訂。輸入『演示酒店機票』可測試卡片流程。"
    state["pending_widgets"]=output_widgets
    state.setdefault("messages",[]).extend([{"role":"user","content":message},{"role":"assistant","content":result["chat_reply"]}])
    result["widgets"]=output_widgets
    return result,state

def check_events(team,demo=False):
    from guardian_skills import run_skills
    data={"message":"天氣 排隊 人潮","city":team["itinerary"]["city"],"mode":"demo" if demo else "real"}
    context=build_context(data,team)
    # Always replan the accepted trip, not an implicit sample itinerary.
    context["itinerary"]=deepcopy(team["itinerary"])
    events=context.get("events",[])
    if not demo:
        import nearby_sources
        nearby=team["itinerary"].get("nearby_plan")
        if nearby:
            req=nearby["request"]
            start=datetime.fromisoformat(req["date"]+"T"+req.get("start_time","10:00"))
            hourly=nearby_sources.weather(data["city"],nearby["origin"],start,start+timedelta(hours=req.get("hours",3)))
            context["weather"]=hourly
            if hourly.get("available") and hourly.get("reminders"):
                events=[{"id":"weather:"+str(team["version"])+":"+str(hourly.get("fetched_at")),"type":"weather","data_kind":"real","source":hourly.get("source"),"source_url":hourly.get("source_url"),"fetched_at":hourly.get("fetched_at"),"reminders":hourly["reminders"]}]
    context["events"]=events
    result=run_skills(data["message"],context,["weather","queue","crowd"])
    result["events"]=events
    return result
