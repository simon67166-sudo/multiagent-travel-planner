"""
编排 Agent -- 星型架构的中枢：维护共享状态、意图识别、调度 4 个子 Agent、汇总生成回复。
对应 docs/agent-interfaces.md 第二、三节的接口契约。

模型档位：MODEL_FULL（全系统推理最重）。
"""

import json
import sys
from pathlib import Path
from typing import Any

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

import llm_tool
import persona
import trip_plan
import widgets
from agents import content_agent, exception_agent, ota_hotel_agent, route_agent


# ---------------------------------------------------------------------------
# 共享状态：编排 Agent 维护，子 Agent 按需读写
# ---------------------------------------------------------------------------
def new_shared_state(user_id: str, scenario: str = "vacation", onboarding_answers: dict | None = None) -> dict:
    """
    scenario: 本次旅行模式（business_trip/vacation/family/solo_adventure...），
      决定 persona 里 scenario_traits 读写哪个场景的值。
    onboarding_answers: 冷启动问卷答案，见 persona.bootstrap_from_onboarding()；
      不传就给一份空白 persona。
    """
    if onboarding_answers:
        persona_obj = persona.bootstrap_from_onboarding(user_id, scenario, onboarding_answers)
    else:
        persona_obj = persona.new_persona(user_id)
    return {
        "user_id": user_id,
        "scenario": scenario,
        "persona": persona_obj,
        "trip_plan": trip_plan.new_trip_plan(trip_id=f"trip-{user_id}"),
    }


# ---------------------------------------------------------------------------
# 意图识别：决定这轮对话要调用哪些子 Agent
# ---------------------------------------------------------------------------
_INTENT_SYSTEM_PROMPT = """你是一个旅游助手的意图识别模块。根据用户消息，判断需要调用下面哪些能力：
- content: 需要内容/攻略推荐（想去哪玩、找地方、找美食）
- route: 需要规划路线/行程安排
- booking: 需要查酒店/机票/门票预订信息
- exception: 用户在问天气/航班延误等突发情况的应对

只输出 JSON 数组，元素是上面几个 key 里符合的（可以多选），不要输出其他任何文字。
例：["content", "route"]
"""


def classify_intent(user_message: str) -> list[str]:
    raw = llm_tool.call_llm(
        [
            {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
    )
    try:
        intents = json.loads(raw.strip().strip("`"))
        assert isinstance(intents, list)
        return intents
    except Exception:
        # 意图识别没解析出来就退化成"只聊天"，不调用任何子 Agent
        return []


# ---------------------------------------------------------------------------
# 主流程：意图识别 → 分发调用 4 个子 Agent → 汇总 → 生成回复
# ---------------------------------------------------------------------------
def orchestrate(user_message: str, shared_state: dict) -> tuple[dict, dict]:
    intents = classify_intent(user_message)

    results: dict[str, Any] = {}
    if "content" in intents:
        results["content"] = content_agent.run(shared_state, location_hint=user_message)
    if "route" in intents:
        # 如果这轮也触发了内容推荐，路线就规划到刚推荐的地点；没有的话 route_agent 内部会用占位地点兜底
        places = [r["place"] for r in results.get("content", {}).get("recommendations", []) if r.get("place")] or None
        results["route"] = route_agent.run(shared_state, places=places)
    if "booking" in intents:
        results["booking"] = ota_hotel_agent.run(shared_state, location=None)
    if "exception" in intents:
        # 占位：拿整句话去匹配行程里的地点名字，真实版本应该先做实体识别抽出具体地点
        results["exception"] = exception_agent.run(shared_state, event_type="unknown", event_detail=user_message)

    # 不需要再手动"写回共享状态"——route_agent/exception_agent 已经直接改了 shared_state["trip_plan"]

    # 展示插件：candidates 直接复用 content_agent 已经算好、排过序的真实推荐结果，
    # 插件只管挑/展示，不重新跑检索逻辑（详见 widgets.py 顶部的设计说明）。
    output_widgets = []
    if "content" in results:
        candidates = results["content"]["recommendations"]
        if candidates:
            output_widgets.append(widgets.build_post_list_widget(candidates))
            output_widgets.append(widgets.build_attraction_picker_widget(candidates))
    if "booking" in results:
        # 两个 widget 各自按 provider_type 从同一份 candidates 里挑，查不到对应类型就返回 None
        booking_candidates = results["booking"]["candidates"]
        for widget in (
            widgets.build_flight_picker_widget(booking_candidates),
            widgets.build_hotel_picker_widget(booking_candidates),
        ):
            if widget is not None:
                output_widgets.append(widget)

    summary_for_llm = json.dumps(results, ensure_ascii=False)
    reply = llm_tool.call_llm(
        [
            {
                "role": "system",
                "content": "你是旅游助手，根据下面的子模块返回结果，给用户一句简短、口语化的中文回复。",
            },
            {"role": "user", "content": f"用户说：{user_message}\n子模块结果：{summary_for_llm}"},
        ]
    )

    output = {
        "chat_reply": reply,
        "community_panel": results.get("content", {}).get("recommendations", []),
        "map_panel": results.get("route", {}),
        "widgets": output_widgets,
    }
    return output, shared_state


if __name__ == "__main__":
    print("意图识别测试:", classify_intent("帮我推荐一下杭州适合玩的地方，顺便排一下路线"))
