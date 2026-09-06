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


def run(shared_state: dict, location_hint: str, top_k: int = 3) -> dict:
    """
    location_hint 目前没用上——它是留给"内容相关性排序"（两阶段检索第二阶段）的输入，
    那部分排序算法还没实现，先按人格相似度原样返回。
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
