"""
达人/内容 Agent -- 两阶段检索第一阶段：按人格向量查 store 里相似人格发过的帖子。

模型档位：MODEL_FULL（UGC 语义提炼，强模型）—— 目前还没实际用到 LLM（纯向量检索），
以后做"内容相关性排序"（两阶段检索第二阶段）时在这个文件里接
llm_tool.call_llm(messages, model=llm_tool.MODEL_FULL)。
"""

import sys
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

import persona
import store

# 港澳达人数据库目前只有这两个城市（见 import_hk_macau_data.py）；杭州那批老 demo 数据没有
# city 字段，不在这个列表里也不受影响——_extract_city 匹配不到就返回 None，不做城市过滤。
_KNOWN_CITIES = ("澳门", "香港")


def _extract_city(location_hint: str | None) -> str | None:
    """从用户消息原文里粗略提取城市名（子串匹配），匹配不到就返回 None（不做城市过滤）。
    跟 exception_agent.py 的子串匹配是同一个"先跑起来，以后再换实体识别"的思路，不是精确 NLP。
    """
    if not location_hint:
        return None
    for city in _KNOWN_CITIES:
        if city in location_hint:
            return city
    return None


def run(shared_state: dict, location_hint: str, top_k: int = 3) -> dict:
    """
    location_hint：用户这轮消息原文，两个用途——
    (1) 粗略提取城市名做硬过滤（子串匹配"澳门"/"香港"，匹配不到就不过滤，兼容没有 city
        字段的老数据）；
    (2) 留给"内容相关性排序"（两阶段检索第二阶段）用，那部分排序算法还没实现。
    """
    vector = persona.compute_persona_vector(shared_state["persona"], shared_state["scenario"])
    city = _extract_city(location_hint)
    posts = store.query_similar_posts(vector, top_k=top_k, city=city)
    return {
        "recommendations": [
            {
                "post_id": p.get("post_id"),
                "place": p.get("place"),
                "time_slot": p.get("time_slot"),
                "avg_cost": p.get("avg_cost"),
                "rating": p.get("rating"),
                "similarity_score": p.get("similarity_score"),
                "avoid_tips": p.get("avoid_tips"),
                "verified_trip": p.get("verified_trip"),
                "caption": p.get("caption"),
                "images": p.get("images", []),
                # 港澳达人数据库字段（见 import_hk_macau_data.py），杭州那批老数据没有这些字段，
                # 会是 None/空列表，前端/widgets.py 按需读取，不强制要求
                "city": p.get("city"),
                "category": p.get("category"),
                "post_type": p.get("post_type"),
                "address": p.get("address"),
                "tags": p.get("tags", []),
                "verified_local": p.get("verified_local", False),
            }
            for p in posts
        ]
    }


if __name__ == "__main__":
    import json

    demo_persona = persona.bootstrap_from_onboarding(
        "content-agent-demo-user",
        "vacation",
        {
            "pace_score": 0.2,
            "budget_score": 0.5,
            "social_mode": "family",
            "interest_theme": ["自然风光"],
            "taste": ["江浙菜"],
            "novelty_score": 0.3,
        },
    )
    vector = persona.compute_persona_vector(demo_persona, "vacation")
    store.add_post(
        "content-agent-demo-post",
        vector,
        {"place": "西湖", "time_slot": "上午", "avg_cost": 120, "rating": 4.7, "verified_trip": True},
    )

    demo_shared_state = {"persona": demo_persona, "scenario": "vacation"}
    print(json.dumps(run(demo_shared_state, location_hint="杭州"), ensure_ascii=False, indent=2))
