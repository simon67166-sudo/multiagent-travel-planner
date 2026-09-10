"""Authenticated team endpoints. Only proposal acceptance updates the official trip."""
from copy import deepcopy
from datetime import datetime
from threading import RLock
import os, time
from urllib.parse import urlsplit
from flask import g, jsonify, request, Blueprint
from guardian_store import GuardianStore, Conflict, Forbidden, canonical_itinerary
_event_lock=RLock()

def current_team(sessions,state):
    team=GuardianStore(sessions.path).ensure(g.session_id,state)
    restore(state,team)
    state["_guardian"]=team
    return team

def restore(state,team):
    state["trip_plan"]=deepcopy(team["itinerary"]["trip_plan"])
    state["city"]=team["itinerary"]["city"]
    for key in ("mode","legacy_nearby_plan","migration_notes"):
        if key in team["itinerary"]: state[key]=deepcopy(team["itinerary"][key])
        else: state.pop(key,None)
    if team["itinerary"].get("nearby_plan"): state["nearby_plan"]=deepcopy(team["itinerary"]["nearby_plan"])
    else: state.pop("nearby_plan",None)

def proposal_for(sessions,state,team,reason,sources=None):
    draft=canonical_itinerary(state)
    proposal=None
    if draft!=team["itinerary"]:
        proposal=GuardianStore(sessions.path).propose(g.session_id,draft,reason,team["version"],team["revision"],sources)
    restore(state,team)
    return proposal

def register(app,get_sessions,new_state):
    api=Blueprint("guardian",__name__)
    def store(): return GuardianStore(get_sessions().path)
    def team(): return store().ensure(g.session_id,get_sessions().load(g.session_id) or new_state())
    def body():
        data=request.get_json(silent=True)
        if not isinstance(data,dict): raise ValueError("請提供 JSON 物件")
        return data
    @app.errorhandler(Conflict)
    def conflict(error): return jsonify(error=str(error)),409
    @app.errorhandler(Forbidden)
    def forbidden(error): return jsonify(error=str(error)),403
    @app.before_request
    def same_origin():
        if request.method in ("POST","PATCH","DELETE","PUT") and request.headers.get("Origin"):
            allowed={request.host_url.rstrip("/"),os.getenv("PUBLIC_BASE_URL","").rstrip("/")}
            if request.headers["Origin"].rstrip("/") not in allowed: raise Forbidden("請從同一個網站提交操作")
    @api.get("/team")
    def get_team(): return jsonify(team())
    @api.post("/team")
    def create_team():
        data=body(); legacy=get_sessions().load(g.session_id) or new_state()
        if store().exists(g.session_id): legacy=store().snapshot(g.session_id)["itinerary"]
        return jsonify(store().create(g.session_id,data.get("name","旅行團隊"),data.get("member_name","發起人"),legacy))
    @api.post("/team/join")
    def join_team():
        data=body()
        return jsonify(store().join(g.session_id,data.get("token"),data.get("name")))
    @api.post("/team/invite")
    def invite():
        team(); data=body()
        if type(data.get("revoke",False)) is not bool: raise ValueError("revoke 必須是布林值")
        origin=os.getenv("PUBLIC_BASE_URL",request.host_url).rstrip("/")
        parsed=urlsplit(origin)
        if parsed.scheme not in ("http","https") or not parsed.netloc: raise ValueError("PUBLIC_BASE_URL 必須為 http(s) 網址")
        token=store().invite(g.session_id,data.get("revoke",False))
        return jsonify(invite_url=origin+"/?join="+token if token else None)
    @api.patch("/team/preferences")
    def preferences():
        from guardian_skills import validate_preferences
        team(); data=body()
        if set(data)-{"preferences","expected_revision"}: raise ValueError("只能修改自己的偏好")
        return jsonify(store().preferences(g.session_id,validate_preferences(data.get("preferences")),data.get("expected_revision")))
    @api.delete("/team/members/<member_id>")
    def remove(member_id):
        team()
        return jsonify(store().remove(g.session_id,member_id))
    @api.get("/proposals")
    def proposals(): return jsonify(proposals=team()["proposals"])
    @api.post("/proposals/<proposal_id>/accept")
    def accept(proposal_id):
        from guardian_service import validate_itinerary
        current=team(); data=body()
        if current["role"]!="owner": raise Forbidden("只有發起人能接受提案")
        proposal=next((p for p in current["proposals"] if p["id"]==proposal_id),None)
        if proposal:
            check=validate_itinerary(proposal["itinerary"],current["members"])
            if check["violations"]: raise ValueError("提案違反硬限制："+"；".join(check["violations"]))
        return jsonify(store().accept(g.session_id,proposal_id,data.get("expected_version")))
    @api.post("/proposals/<proposal_id>/reject")
    def reject(proposal_id):
        team()
        return jsonify(store().reject(g.session_id,proposal_id))
    @api.post("/guardian/plan")
    def plan():
        from guardian_service import execute
        data=body()
        with get_sessions().edit(g.session_id,new_state) as state:
            current=current_team(get_sessions(),state)
            output=execute(data,current)
            draft=output.pop("proposed_itinerary",None)
            if draft:
                output["proposal"]=store().propose(g.session_id,draft,"旅行技能協作提案",current["version"],current["revision"],output.get("sources"))
            state.setdefault("messages",[]).extend([{"role":"user","content":data["message"]},{"role":"assistant","content":output["chat_reply"]}])
            output.update(version=current["version"],revision=current["revision"])
        return jsonify(output)
    @api.get("/guardian/skills")
    def skills():
        from guardian_skills import SKILLS
        return jsonify(skills=SKILLS)
    @api.post("/events/check")
    def events():
        from guardian_service import check_events
        current=team(); data=body(); demo=data.get("demo",False)
        if type(demo) is not bool: raise ValueError("demo 必須為布林值")
        key=("demo" if demo else "real")+":"+str(current["version"])+":"+str(current["revision"])
        with _event_lock:
            now=time.time(); cached=store().cached_check(g.session_id,key,now)
            if cached: return jsonify({**cached,"cached":True,"proposals":store().snapshot(g.session_id)["proposals"]})
            output=check_events(current,demo=demo)
            store().record_events(g.session_id,output.get("events",[]))
            draft=output.pop("proposed_itinerary",None)
            proposal=store().propose(g.session_id,draft,"環境事件調整提案",current["version"],current["revision"],output.get("sources"),event_key=key) if draft else None
            output.update(proposals=[proposal] if proposal else [],checked_at=datetime.now().astimezone().isoformat(),cached=False)
            store().save_check(g.session_id,key,now,output)
            return jsonify(output)
    app.register_blueprint(api)
