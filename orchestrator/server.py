"""
本地演示用的 Web 服务器 -- 把 main.py 的编排 Agent 包装成 HTTP 接口，
给 web/index.html 这个前端页面调用（左：实时日程，右：聊天框）。

范围说明：
- 只做单会话的本地 demo（一个进程内全局 shared_state，没有登录/多用户/并发处理），
  仅供本地开发/演示用，不要直接这样部署到公网。
- 前端 -> 后端只有两个接口：GET /trip 拿当前完整行程，POST /chat 发一句话给编排 Agent。
- 社区面板/地图面板（agent-architecture.md 里的三面板设计）这版没做，
  先做"日程 + 聊天"两栏验证编排 Agent 真的能驱动前端展示，其余面板以后再加。

依赖：pip install flask
运行：python orchestrator/server.py，然后浏览器打开 http://127.0.0.1:5000
"""

from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

import main
import trip_plan

app = Flask(__name__, static_folder=None)

_WEB_DIR = Path(__file__).parent / "web"

# demo 用的固定人格，实际接产品时这里应该换成真实登录用户 + onboarding 流程
_DEMO_ONBOARDING_ANSWERS = {
    "pace_score": 0.4,
    "budget_score": 0.5,
    "social_mode": "family",
    "interest_theme": ["自然风光", "美食探店"],
    "taste": ["江浙菜"],
    "novelty_score": 0.3,
}

_state = main.new_shared_state(
    "web-demo-user", scenario="vacation", onboarding_answers=_DEMO_ONBOARDING_ANSWERS
)


@app.get("/")
def index():
    return send_from_directory(_WEB_DIR, "index.html")


@app.get("/trip")
def get_trip():
    return jsonify(trip_plan.render(_state["trip_plan"]))


@app.post("/chat")
def chat():
    payload = request.get_json(force=True, silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message 不能为空"}), 400

    output, _ = main.orchestrate(message, _state)
    return jsonify(output)


if __name__ == "__main__":
    # debug=False：这个服务准备通过 Cloudflare Tunnel 暴露到公网给队友访问，
    # Werkzeug 的调试器（debug=True）在公网环境下有远程执行代码的风险，不能开。
    app.run(debug=False, port=5000)
