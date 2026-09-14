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
from agents import exception_agent, ota_hotel_agent, route_agent

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

def _trip_fully_booked(trip_plan_obj: dict) -> bool:
    """机票、酒店、每日行程是不是都定完了——exception_agent.check_itinerary_weather() 的
    触发条件（用户要求"已经制定完了机票酒店行程后"才查天气，不是随便哪个环节都查）。"""
    return bool(trip_plan_obj.get("flights")) and bool(trip_plan_obj.get("hotels")) and bool(trip_plan_obj.get("days"))


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
        chat_reply = apply_selection(widget, selected, state)
        state["pending_widgets"] = [w for w in state["pending_widgets"] if w.get("widget") != widget]

        # 2026-09-17 用户提出：机票酒店行程都定完之后，该调 exception_agent 查一次真实天气，
        # 在行程里标出可能下雨/下雪的天，雨大的话问一下要不要调整——只在"刚好凑齐"那一次
        # 检查（state["weather_checked"] 记过就不再重复查，不然每多订一次东西都要重查一遍，
        # 真实 API 调用没必要，回复也会一直重复同样的天气提醒）
        if not state.get("weather_checked") and _trip_fully_booked(state["trip_plan"]):
            weather_result = exception_agent.check_itinerary_weather(state)
            state["weather_checked"] = True
            if weather_result["day_weather"]:
                summaries = []
                for m in weather_result["day_weather"].values():
                    detail = "有降雪" if m["is_snow"] else f"降雨概率约 {round(m['max_rain_probability'] * 100)}%"
                    summaries.append(f"{m['date']}{detail}")
                weather_note = "机票酒店行程都定好了，顺手查了一下天气：" + "；".join(summaries)
                if weather_result["suggested_adjustment"]:
                    weather_note += "\n" + weather_result["suggested_adjustment"]
                chat_reply = (chat_reply + "\n" + weather_note) if chat_reply else weather_note

        if chat_reply:
            # 交互完了不能就这么悄无声息——排班/订票订房这些后台真做了事，聊天框得有句话
            # 衔接一下，跟 POST /chat 走的助手回复存进同一个 messages 列表，前端/下次刷新
            # 页面都能看到，不是只在这次响应里一次性带一下就消失
            state["messages"] = state.get("messages", []) + [{"role": "assistant", "content": chat_reply}]
    return jsonify({"status": "ok", "chat_reply": chat_reply})


def apply_selection(widget, selected, state) -> str | None:
    """落地用户在 widget 里的选择，返回一句衔接用的助手回复文字（没有话说就 None）——
    调用方负责把这句话存进 state["messages"] 并回传给前端，做成聊天气泡而不是让交互
    结束后聊天框毫无反应。"""
    if widget == "attraction_picker":
        # 2026-09-15 起，排时间的触发点从"意图分类猜中了 route"改成"用户在这里选完确认"
        # ——这是确定性动作，不会因为同一句话不同轮调用而结果不一样。走真正的骨架排班
        # （route_agent.schedule()），不是老接口 route_agent.run() 那种单一占位日期顺序追加。
        #
        # 2026-09-17 修正：排班不能只喂 picks（用户在卡片里勾的那几个）——attraction_picker
        # 的候选池本来就只是达人 Agent 全量候选里精选出来展示的一小撮（pool_size=10），用户
        # 从中最多再勾 6 个，如果排班只看得到这几个，早餐/午晚餐/用户没勾的其它景点槽位就
        # 完全没有候选可用，只能空着。正确做法是把达人 Agent 给的全量候选池（存在
        # shared_state["last_content_candidates"] 里，见 orchestrator_agent.py）整个交给
        # route_agent.schedule()，用户勾选的那几个只是"优先"（priority_places），不是"唯一"。
        # 取不到全量池（比如老会话没这个字段）就退回只用 picks，不炸。
        #
        # 2026-09-17 再修正：确认一次该排够整趟行程该有的天数，不是每次只排 1 天——之前
        # time_budget_days 写死 1，用户说"3天行程"确认一次却只排出 day-1，得再确认 2 次
        # 才能凑够 3 天，跟"一开始就说了3天"的预期不符。天数是达人 Agent 提取到的
        # （content_agent._extract_day_count()，见 orchestrator_agent.py 存的
        # last_trip_day_count），不是这里瞎猜的。
        picks = [item for item in selected if item.get("place")]
        if not picks:
            return None
        priority_places = {item["place"] for item in picks}
        full_pool = state.get("last_content_candidates") or picks
        result = route_agent.schedule(
            state, full_pool, city=state.get("city", _DEMO_CITY), mode="trip",
            time_budget_days=state.get("last_trip_day_count", 1),
            priority_places=priority_places,
        )
        scheduled_days = sorted(day for day, stops in result["days"].items() if stops)
        if not scheduled_days:
            return "这几个地点这次没能排进行程（坐标缺失或者当天槽位已经满了），可以再选几个试试。"
        scheduled_places = {stop.get("place") for stops in result["days"].values() for stop in stops}
        places = "、".join(p for p in (item.get("place") for item in picks) if p)
        reply = f"已经把 {places} 排进 {'、'.join(scheduled_days)} 啦，来看看左边的行程时间线吧。"
        missed_picks = [p for p in (item.get("place") for item in picks) if p and p not in scheduled_places]
        if missed_picks:
            reply += f"其中「{'、'.join(missed_picks)}」这次没能排进去（时段冲突或坐标缺失），可以再选一轮试试。"
        if result.get("weather_reminders"):
            reply += "\n" + "\n".join(result["weather_reminders"])
        return reply
    elif widget == "flight_picker":
        for item in selected:
            trip_plan.add_flight(
                state["trip_plan"],
                {
                    "flight_no": item.get("flight_no"),
                    "from_": item.get("from_"),
                    "to": item.get("to"),
                    # 2026-09-17 修正：candidate 里本来就带 "date"（ota_hotel_agent.run() 算的
                    # 去程/回程日期），之前这里没往下传，存进 trip_plan.flights 的机票就丢了
                    # 日期，前端"机票"卡片自然显示不出来——不是前端没做，是数据在这里断了
                    "date": item.get("date"),
                    "depart_time": item.get("depart_time"),
                    "arrive_time": item.get("arrive_time"),
                    "status": item.get("status"),
                },
            )
        flight_nos = "、".join(item.get("flight_no") for item in selected if item.get("flight_no"))
        return f"已经帮你订好 {flight_nos} 啦，行程里的机票信息更新好了。"
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
                    # 真实坐标（ota_hotel_agent._search_real_hotels() 本来就带了）——存进
                    # trip_plan 才有数据给 ota_hotel_agent._existing_hotel_distance_check() 用，
                    # 判断这家已订酒店离行程终点远不远
                    "lng": item.get("lng"),
                    "lat": item.get("lat"),
                },
            )
        names = "、".join(item.get("name") for item in selected if item.get("name"))
        reply = f"已经帮你订好 {names} 啦。"
        reminder = ota_hotel_agent._existing_hotel_distance_check(state["trip_plan"])
        if reminder:
            reply += "\n" + reminder
        return reply
    else:
        raise RuntimeError("Unsupported widget")


if __name__ == "__main__":
    # debug=False：这个服务准备通过 Cloudflare Tunnel 暴露到公网给队友访问，
    # Werkzeug 的调试器（debug=True）在公网环境下有远程执行代码的风险，不能开。
    app.run(debug=False, port=5000)
