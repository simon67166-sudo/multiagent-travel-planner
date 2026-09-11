"""
本地演示用的 Web 服务器 -- 把 main.py 的编排 Agent 包装成 HTTP 接口，
给 web/index.html 这个前端页面调用（左：实时日程，右：聊天框）。

范围说明：
- SQLite 保存独立浏览器会话；单进程本地 demo，尚未提供帐号登入或多进程并发控制，
  仅供本地开发/演示用，不要直接这样部署到公网。
- 前端 -> 后端三个接口：
    GET  /trip           拿当前完整行程（含 schedule_widgets 的地图/时间线/机票酒店面板数据）
    POST /chat           发一句话给编排 Agent，返回值带 widgets 数组（右侧聊天插件数据）
    POST /widget-response 用户在右侧"选择型" widget（attraction_picker/flight_picker/
                          hotel_picker）里勾完之后，前端把选中项原样传回来，这里落地：
                          景点 -> route_agent 排进行程；机票/酒店 -> trip_plan.add_flight()/
                          add_hotel() 确认预订。
- 社区面板（agent-architecture.md 三面板设计里的社区帖子墙）这版没做，聊天框里的
  post_list/attraction_picker 算是这块的雏形。

依赖：pip install flask python-dotenv
运行前准备：orchestrator/.env 里除了 PARATERA_API_KEY/AMAP_KEY，还要加：
  AMAP_JS_KEY=你的高德 "Web端(JS API)" Key（前端画地图用，注意这跟 AMAP_KEY 是不同类型的 key）
  AMAP_JS_SECURITY_CODE=你的安全密钥（高德 JS API 2.0 版本强制要求，在申请 JS API key 的
                                    同一个应用页面能找到，没配置的话地图初始化会报错）
运行：python orchestrator/server.py，然后浏览器打开 http://127.0.0.1:5000
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, g
from uuid import uuid4
import re
from session_store import SessionStore
from openai import OpenAIError

import main
import schedule_widgets
import trip_plan
from agents import route_agent

load_dotenv(Path(__file__).parent / ".env")

app = Flask(__name__, static_folder=None)

_WEB_DIR = Path(__file__).parent / "web"

# demo 用的固定人格/城市，实际接产品时这里应该换成真实登录用户 + onboarding 流程
_DEMO_ONBOARDING_ANSWERS = {
    "pace_score": 0.4,
    "budget_score": 0.5,
    "social_mode": "family",
    "interest_theme": ["自然风光", "美食探店"],
    "taste": ["江浙菜"],
    "novelty_score": 0.3,
}
_DEMO_CITY = "澳门"

session_store = SessionStore()
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

@app.before_request
def identify_session():
    candidate = request.cookies.get("travel_session", "")
    g.session_id = candidate if re.fullmatch(r"[0-9a-f]{32}", candidate) else uuid4().hex

@app.after_request
def persist_session_cookie(response):
    response.set_cookie("travel_session", g.session_id, max_age=30 * 86400,
                        httponly=True, samesite="Lax", secure=request.is_secure)
    response.headers["Cache-Control"] = "no-store"
    return response

def new_state():
    state = main.new_shared_state(g.session_id, scenario="vacation",
                                  onboarding_answers=_DEMO_ONBOARDING_ANSWERS)
    state["city"] = _DEMO_CITY
    return state

@app.errorhandler(RuntimeError)
@app.errorhandler(OpenAIError)
def model_error(error):
    # 不把模型服务商的原始响应体/key/prompt 内容暴露给前端
    return jsonify({"error": "模型或工具暂时无法完成，本轮未保存，请重试。"}), 502

@app.get("/session")
def get_session():
    with session_store.edit(g.session_id, new_state) as state:
        messages = [{"role": m["role"], "content": m["content"]}
                    for m in state.get("messages", [])
                    if m.get("role") in ("user", "assistant") and m.get("content") and not m.get("tool_calls")]
        result = {"messages": messages, "widgets": state.get("pending_widgets", [])}
        return jsonify(result)

@app.get("/")
def index():
    html = (_WEB_DIR / "index.html").read_text(encoding="utf-8")
    # 高德 JS API key 不写进 index.html 明文（那个文件是要提交进 git 的），
    # 用占位符 + 服务端渲染时注入，key 本身留在 .env 里（已 gitignore）。
    if not os.environ.get("AMAP_JS_KEY"):
        html = html.replace('<script src="https://webapi.amap.com/maps?v=2.0&key=__AMAP_JS_KEY__"></script>', "")
    html = html.replace("__AMAP_JS_KEY__", os.environ.get("AMAP_JS_KEY", ""))
    html = html.replace("__AMAP_JS_SECURITY_CODE__", os.environ.get("AMAP_JS_SECURITY_CODE", ""))
    return html


@app.get("/trip")
def get_trip():
    state = session_store.load(g.session_id) or new_state()
    trip = state["trip_plan"]
    rendered = trip_plan.render(trip)
    rendered["trip_map"] = schedule_widgets.build_trip_map_widget(trip, city=state.get("city", _DEMO_CITY))
    rendered["day_timeline"] = schedule_widgets.build_day_timeline_widget(trip)
    rendered["booking_panel"] = schedule_widgets.build_booking_panel_widget(trip)
    return jsonify(rendered)


@app.post("/chat")
def chat():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("message"), str):
        return jsonify({"error": "message 必须是文字"}), 400
    message = payload["message"].strip()
    if not message or len(message) > 8000:
        return jsonify({"error": "message 长度必须是 1 到 8000 字"}), 400
    with session_store.edit(g.session_id, new_state) as state:
        output, _ = main.orchestrate(message, state)
    return jsonify(output)


@app.errorhandler(ValueError)
def validation_error(error):
    return jsonify({"error": str(error)}), 400

@app.post("/widget-response")
def widget_response():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "请提供 JSON 物件"}), 400
    widget = payload.get("widget")
    selected = payload.get("selected")
    if not isinstance(widget, str) or not isinstance(selected, list) or not selected or not all(isinstance(item, dict) for item in selected):
        return jsonify({"error": "无效的选择资料"}), 400
    with session_store.edit(g.session_id, new_state) as state:
        offered = next((w for w in state.get("pending_widgets", []) if w.get("widget") == widget), None)
        if not offered or len(selected) > offered["data"].get("max_select", 1):
            return jsonify({"error": "卡片已失效或超出选择数量"}), 400
        options = offered["data"].get("options", [])
        if any(item not in options for item in selected) or any(item in selected[:i] for i, item in enumerate(selected)):
            return jsonify({"error": "选项不在本次候选清单"}), 400
        apply_selection(widget, selected, state)
        state["pending_widgets"] = [w for w in state["pending_widgets"] if w.get("widget") != widget]
    return jsonify({"status": "ok"})


def apply_selection(widget, selected, state):
    if widget == "attraction_picker":
        # 2026-09-15 起，排时间的触发点从"意图分类猜中了 route"改成"用户在这里选完确认"
        # ——这是确定性动作，不会因为同一句话不同轮调用而结果不一样。走真正的骨架排班
        # （route_agent.schedule()），不是老接口 route_agent.run() 那种单一占位日期顺序追加；
        # picks 已经是完整候选字典（带 lng/lat/category），content_agent.run() 早就查好了，
        # schedule() 直接能用
        picks = [item for item in selected if item.get("place")]
        if picks:
            route_agent.schedule(state, picks, city=state.get("city", _DEMO_CITY), mode="trip", time_budget_days=1)
    elif widget == "flight_picker":
        for item in selected:
            trip_plan.add_flight(
                state["trip_plan"],
                {
                    "flight_no": item.get("flight_no"),
                    "from_": item.get("from_"),
                    "to": item.get("to"),
                    "depart_time": item.get("depart_time"),
                    "arrive_time": item.get("arrive_time"),
                    "status": item.get("status"),
                },
            )
    elif widget == "hotel_picker":
        for item in selected:
            trip_plan.add_hotel(
                state["trip_plan"],
                {
                    "name": item.get("name"),
                    "address": item.get("address"),
                    "check_in": item.get("check_in"),
                    "check_out": item.get("check_out"),
                    "price": item.get("price"),
                    "cancel_policy": item.get("cancel_policy"),
                },
            )
    else:
        raise RuntimeError("Unsupported widget")


if __name__ == "__main__":
    # debug=False：这个服务准备通过 Cloudflare Tunnel 暴露到公网给队友访问，
    # Werkzeug 的调试器（debug=True）在公网环境下有远程执行代码的风险，不能开。
    app.run(debug=False, port=5000)
