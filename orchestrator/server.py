"""
本地演示用的 Web 服务器 -- 把 main.py 的编排 Agent 包装成 HTTP 接口，
给 web/index.html 这个前端页面调用（左：实时日程，右：聊天框）。

范围说明：
- 只做单会话的本地 demo（一个进程内全局 shared_state，没有登录/多用户/并发处理），
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
from flask import Flask, jsonify, request

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
_DEMO_CITY = "杭州"

_state = main.new_shared_state(
    "web-demo-user", scenario="vacation", onboarding_answers=_DEMO_ONBOARDING_ANSWERS
)


@app.get("/")
def index():
    html = (_WEB_DIR / "index.html").read_text(encoding="utf-8")
    # 高德 JS API key 不写进 index.html 明文（那个文件是要提交进 git 的），
    # 用占位符 + 服务端渲染时注入，key 本身留在 .env 里（已 gitignore）。
    html = html.replace("__AMAP_JS_KEY__", os.environ.get("AMAP_JS_KEY", ""))
    html = html.replace("__AMAP_JS_SECURITY_CODE__", os.environ.get("AMAP_JS_SECURITY_CODE", ""))
    return html


@app.get("/trip")
def get_trip():
    trip = _state["trip_plan"]
    rendered = trip_plan.render(trip)
    rendered["trip_map"] = schedule_widgets.build_trip_map_widget(trip, city=_DEMO_CITY)
    rendered["day_timeline"] = schedule_widgets.build_day_timeline_widget(trip)
    rendered["booking_panel"] = schedule_widgets.build_booking_panel_widget(trip)
    return jsonify(rendered)


@app.post("/chat")
def chat():
    payload = request.get_json(force=True, silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message 不能为空"}), 400

    output, _ = main.orchestrate(message, _state)
    return jsonify(output)


@app.post("/widget-response")
def widget_response():
    """
    请求体：{"widget": "attraction_picker"|"flight_picker"|"hotel_picker", "selected": [...]}
    selected 里的对象原样是前端从对应 widget 的 options 里拿到的候选（不是前端自己编的），
    这样后端不用重新校验内容真实性——跟 widgets.py 里"LLM 只能选真实 ID"是同一个防幻觉思路。
    """
    payload = request.get_json(force=True, silent=True) or {}
    widget = payload.get("widget")
    selected = payload.get("selected") or []

    if widget == "attraction_picker":
        places = [item.get("place") for item in selected if item.get("place")]
        if places:
            route_agent.run(_state, places=places, city=_DEMO_CITY)
    elif widget == "flight_picker":
        for item in selected:
            trip_plan.add_flight(
                _state["trip_plan"],
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
                _state["trip_plan"],
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
        return jsonify({"error": f"未知的 widget 类型: {widget}"}), 400

    return jsonify({"status": "ok"})


if __name__ == "__main__":
    # debug=False：这个服务准备通过 Cloudflare Tunnel 暴露到公网给队友访问，
    # Werkzeug 的调试器（debug=True）在公网环境下有远程执行代码的风险，不能开。
    app.run(debug=False, port=5000)
