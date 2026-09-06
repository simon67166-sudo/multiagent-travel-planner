"""
编排 Agent 骨架 -- 星型架构

范围说明：
- 本文件搭"Agent 间交互"的骨架（编排 Agent 调度 4 个子 Agent 的流程），
  对应 docs/agent-interfaces.md 第二、三节的接口契约。
- persona（人格）、store（达人社区/历史）、trip_plan（行程/机票/酒店/天气异常）
  三个模块已经接进来了，见下面各占位子 Agent 里的调用。仍然待做的部分见
  docs/orchestrator-guide.md 的"待接事项"。
- agent_ota_hotel 还是纯占位假数据（没有真实的比价/查库存来源）；
  agent_route/agent_exception/agent_content 已经在操作真实的共享状态
  （persona 向量、trip_plan 的行程节点），只是"路线怎么规划""异常怎么判定"
  这些具体算法还很简单，等真实 Agent 实现替换即可，字段结构不用变。

运行前准备：
- 在 orchestrator/.env 里写一行 PARATERA_API_KEY=你的key （这个文件已加进 .gitignore，不会被提交）
- pip install openai python-dotenv chromadb
"""

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

import persona
import store
import trip_plan

load_dotenv(Path(__file__).parent / ".env")

BASE_URL = "https://llmapi.paratera.com/v1"
# 两档模型对齐 agent-architecture.md 的"模型档位"设计：
# MODEL_FULL  (全量模型) -- 编排 Agent、达人/内容 Agent 用，推理/语义理解重的场景
# MODEL_LIGHT (轻量模型) -- 行程/路线 Agent、OTA/酒店 Agent 用，结构化/低复杂度场景
MODEL_FULL = "DeepSeek-V4-Pro"
MODEL_LIGHT = "DeepSeek-V4-Flash"

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("PARATERA_API_KEY")
        if not api_key:
            raise RuntimeError("环境变量 PARATERA_API_KEY 未设置，先设置好 API Key 再运行。")
        _client = OpenAI(api_key=api_key, base_url=BASE_URL)
    return _client


def call_llm(messages: list[dict], model: str = MODEL_FULL, **kwargs) -> str:
    resp = _get_client().chat.completions.create(model=model, messages=messages, **kwargs)
    return resp.choices[0].message.content


# ---------------------------------------------------------------------------
# 共享状态：编排 Agent 维护，子 Agent 按需读写
# persona 用 persona.py 的结构，trip_plan 用 trip_plan.py 的结构（不再是简化占位）
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
    raw = call_llm(
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
# 子 Agent -- 字段对齐 docs/agent-interfaces.md 第二节。
# 换成真实实现、要接 LLM 时对应用哪档模型（见上面 MODEL_FULL/MODEL_LIGHT）：
#   agent_content   -> MODEL_FULL  （UGC 语义提炼，强模型）
#   agent_route     -> MODEL_LIGHT （路线算法包装，轻量模型）
#   agent_ota_hotel -> MODEL_LIGHT （结构化查库存比价，轻量模型）
#   agent_exception -> 架构文档里是"中等模型"，目前只有两档，先待定
# ---------------------------------------------------------------------------
def agent_content(shared_state: dict, location_hint: str, top_k: int = 3) -> dict:
    """
    真实检索：用 persona 向量查 store 里相似人格发过的帖子（两阶段检索第一阶段）。
    location_hint 目前没用上——它是留给"内容相关性排序"（两阶段检索第二阶段）的输入，
    那部分排序算法还没实现，先按相似度原样返回。
    """
    vector = persona.compute_persona_vector(shared_state["persona"], shared_state["scenario"])
    posts = store.query_similar_posts(vector, top_k=top_k)
    return {
        "recommendations": [
            {
                "place": p.get("place"),
                "time_slot": p.get("time_slot"),
                "avg_cost": p.get("avg_cost"),
                "rating": p.get("rating"),
                "similarity_score": p.get("similarity_score"),
                "avoid_tips": p.get("avoid_tips"),
                "verified_trip": p.get("verified_trip"),
                "caption": p.get("caption"),
                "images": p.get("images", []),
            }
            for p in posts
        ]
    }


_PLACEHOLDER_DAY = "day-1"  # 占位：还没做真正的多日期规划，先都写进同一天


def agent_route(shared_state: dict, places: list[str] | None, time_budget: str | None = None) -> dict:
    """
    真实写入：把 places 依次追加成 trip_plan 里某一天的行程节点（链表结构，见 trip_plan.py）。
    "怎么排序/怎么估算交通方式和时间"这些还是占位（时间字段先填占位文字），
    真正的路线规划算法由行程 Agent 以后接管，写入的还是同一个 trip_plan 结构。
    """
    day_plan = trip_plan.get_or_create_day(shared_state["trip_plan"], _PLACEHOLDER_DAY)

    prev_id = day_plan["head_id"]
    while prev_id and day_plan["nodes"][prev_id]["next_id"]:
        prev_id = day_plan["nodes"][prev_id]["next_id"]

    for place in places or ["占位地点 A"]:
        node_id = f"{_PLACEHOLDER_DAY}-node-{len(day_plan['nodes']) + 1}"
        trip_plan.add_stop(
            day_plan,
            node_id,
            "attraction",
            place,
            arrival_transport="待定",
            arrival_time="待定",
            end_time="待定",
            after_id=prev_id,
        )
        prev_id = node_id

    return {"route": trip_plan.day_stops(day_plan)}


def agent_ota_hotel(
    shared_state: dict, location: str | None, date_range: str | None = None, category: str | None = None
) -> dict:
    # TODO: 还是纯占位假数据，没有真实的比价/查库存来源可接。
    # 真正确认预订后应该调 trip_plan.add_hotel(shared_state["trip_plan"], {...}) 落地。
    return {
        "candidates": [
            {
                "name": "占位酒店/票务 X",
                "price": 399,
                "inventory": 5,
                "cancel_policy": "24小时内免费取消",
                "rating": 4.5,
                "provider_type": "hotel",
            }
        ]
    }


def agent_exception(shared_state: dict, event_type: str, event_detail: str | None = None) -> dict:
    """
    真实处理：event_detail 按地点名字匹配 trip_plan 里所有天的行程节点，
    命中就删掉该节点（trip_plan.remove_stop 会自动重连前后节点）并记一条天气异常。
    "怎么判断某条消息对应哪个地点/要不要真的删"这类判断逻辑很粗糙（纯子串匹配），
    真实的异常应变 Agent 接管后可以换成更靠谱的实现，返回字段不用变。
    """
    trip = shared_state["trip_plan"]
    affected: list[str] = []

    if event_detail:
        for day_plan in trip["days"].values():
            for node_id, node in list(day_plan["nodes"].items()):
                if node["place"] in event_detail:
                    trip_plan.remove_stop(day_plan, node_id)
                    affected.append(node["place"])

    trip_plan.add_weather_alert(
        trip,
        {
            "location": event_detail,
            "event_type": event_type,
            "severity": "未知",
            "description": f"触发异常应变：{event_type}",
            "affects_dates": list(trip["days"].keys()),
        },
    )

    return {
        "needs_replan": bool(affected),
        "suggested_adjustment": f"已从行程中移除受影响地点：{affected}" if affected else "未在当前行程中找到受影响地点，暂不需要调整",
        "affected_locations": affected,
    }


# ---------------------------------------------------------------------------
# 编排 Agent 主流程：对应 docs/agent-interfaces.md 第三节的节点拓扑
# ---------------------------------------------------------------------------
def orchestrate(user_message: str, shared_state: dict) -> tuple[dict, dict]:
    intents = classify_intent(user_message)

    results: dict[str, Any] = {}
    if "content" in intents:
        results["content"] = agent_content(shared_state, location_hint=user_message)
    if "route" in intents:
        # 如果这轮也触发了内容推荐，路线就规划到刚推荐的地点；没有的话 agent_route 内部会用占位地点兜底
        places = [r["place"] for r in results.get("content", {}).get("recommendations", []) if r.get("place")] or None
        results["route"] = agent_route(shared_state, places=places)
    if "booking" in intents:
        results["booking"] = agent_ota_hotel(shared_state, location=None)
    if "exception" in intents:
        # 占位：拿整句话去匹配行程里的地点名字，真实版本应该先做实体识别抽出具体地点
        results["exception"] = agent_exception(shared_state, event_type="unknown", event_detail=user_message)

    # 不需要再手动"写回共享状态"——agent_route/agent_exception 已经直接改了 shared_state["trip_plan"]

    summary_for_llm = json.dumps(results, ensure_ascii=False)
    reply = call_llm(
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
    }
    return output, shared_state


if __name__ == "__main__":
    # 先造 demo 用户的 persona，再往社区库里塞一条"跟他很像的人"发的帖子，
    # 这样 agent_content 才有真实候选可查（不是空的）。
    onboarding_answers = {
        "pace_score": 0.2,
        "budget_score": 0.5,
        "social_mode": "family",
        "interest_theme": ["自然风光", "美食探店"],
        "taste": ["江浙菜"],
        "novelty_score": 0.3,
    }
    state = new_shared_state("demo-user", scenario="vacation", onboarding_answers=onboarding_answers)

    demo_persona = persona.bootstrap_from_onboarding("demo-neighbor", "vacation", onboarding_answers)
    demo_vector = persona.compute_persona_vector(demo_persona, "vacation")
    store.add_post(
        "demo-seed-post",
        demo_vector,
        {
            "place": "西湖",
            "time_slot": "上午",
            "avg_cost": 120,
            "rating": 4.7,
            "avoid_tips": "旺季排队较久",
            "verified_trip": True,
            "caption": "和你人格很像的一位游客发的帖子",
        },
    )

    print("=== 第一轮：推荐 + 规划路线 ===")
    message = "帮我推荐一下杭州适合玩的地方，顺便排一下路线"
    output, state = orchestrate(message, state)
    print(json.dumps(output, ensure_ascii=False, indent=2))

    print("\n=== 第二轮：异常应变（西湖临时封闭）===")
    message2 = "刚看到通知，西湖今天临时封闭施工了"
    output2, state = orchestrate(message2, state)
    print(json.dumps(output2, ensure_ascii=False, indent=2))

    print("\n=== 当前完整行程（trip_plan.render）===")
    print(json.dumps(trip_plan.render(state["trip_plan"]), ensure_ascii=False, indent=2))
