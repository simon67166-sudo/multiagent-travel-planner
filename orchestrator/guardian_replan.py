"""Recompute a proposed sequence using provider legs, never straight-line fallbacks."""
from copy import deepcopy
from datetime import datetime,timedelta
import math
from guardian_store import canonical_itinerary

def finite(value):
    try: return type(value) in (int,float) and math.isfinite(value) and value>=0
    except OverflowError: return False

def rebuild(itinerary,route_fn=None):
    result=deepcopy(itinerary)
    plan=result.get("nearby_plan")
    if not isinstance(plan,dict) or not plan.get("stops"): return result
    if plan.get("demo") or result.get("mode")=="demo":
        # Fictional fixtures have no routable coordinates. Retain visible unknowns.
        plan["legs"]=[]
        plan["travel_times_available"]=False
        return canonical_itinerary(result)
    if route_fn is None:
        from guardian_sources import route as route_fn
    req=plan.get("request",{})
    origin=plan.get("origin")
    if not origin or plan.get("crs")!="GCJ02":
        plan["legs"]=[]; plan["travel_times_available"]=False
        for stop in plan.get("stops",[]): stop.update(arrival_time="待確認",end_time="待確認")
        return canonical_itinerary(result)
    try:
        start=datetime.fromisoformat(req["date"]+"T"+req["start_time"])
        hours=req.get("hours",3)
        if not finite(hours) or hours==0: raise ValueError("invalid hours")
        deadline=start+timedelta(hours=hours)
    except (KeyError,ValueError,TypeError,OverflowError):
        raise ValueError("重新規劃需要有效日期、開始時間及時長") from None
    cursor=start; current=origin; timed=True; legs=[]; violations=[]
    for stop in plan.get("stops",[]):
        if not all(k in stop for k in ("lat","lng")):
            leg={"available":False,"coordinates":[],"steps":[],"reason":"地點座標未知"}
        else: leg=route_fn(current,stop,result["city"],req.get("mode","walking"))
        leg=deepcopy(leg)
        leg.update(from_name=current.get("name"),to_name=stop.get("name"),mode=req.get("mode","walking"))
        duration=leg.get("duration_min")
        known=leg.get("available") is True and finite(duration)
        stay=stop.get("suggested_stay_minutes",30)
        if not finite(stay) or stay>600: raise ValueError("停留時間無效")
        if timed and known:
            arrival=cursor+timedelta(minutes=duration)
            finish=arrival+timedelta(minutes=stay)
            if stop.get("booking_ref"):
                try:
                    booked=datetime.fromisoformat(req["date"]+"T"+stop["arrival_time"])
                    booked_end=datetime.fromisoformat(req["date"]+"T"+stop["end_time"])
                    if arrival>booked: violations.append(stop.get("name","地點")+"無法按預約時間抵達")
                    arrival=max(arrival,booked);finish=booked_end
                except (KeyError,ValueError): violations.append("已預約地點的固定時間待確認")
            stop.update(arrival_time=arrival.strftime("%H:%M"),end_time=finish.strftime("%H:%M"))
            cursor=finish
            if finish>deadline: violations.append("行程超出可用時段")
        else:
            timed=False
            if not stop.get("booking_ref"): stop.update(arrival_time="待確認交通時間",end_time="待確認")
        stop["suggested_stay_minutes"]=stay
        legs.append(leg);current=stop
    plan.update(legs=legs,travel_times_available=timed,schedule_verified=False,
        schedule_violations=sorted(set(violations)),walking_complete=False,cost_complete=False)
    # Availability is still provisional: venue opening hours and inside walking may be unknown.
    return canonical_itinerary(result)
