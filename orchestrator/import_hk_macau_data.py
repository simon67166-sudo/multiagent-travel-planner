"""
把 `standardize_hk_macau_data.py` 生成的标准化数据库
（`orchestrator/data_sources/hk_macau_posts.json`）灌进 Chroma。

跟标准化脚本分开两步：标准化只做字段整理 + 编号，不碰向量库；这个脚本才是真正
"人格向量怎么算 + 写进 store.py"的地方，调过一次以后改人格推断逻辑重跑这个脚本
就行，不用重新解析 Excel。

场景固定用 "vacation"（跟 main.py demo、store.py 自测用的场景一致），因为这批数据
本身就是"旅游达人帖子"，不是分场景收集的。
"""

import json
from pathlib import Path

import persona
import store

_DATA_FILE = Path(__file__).resolve().parent / "data_sources" / "hk_macau_posts.json"
_SCENARIO = "vacation"


def import_posts() -> int:
    records = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    for record in records:
        vector = persona.infer_post_persona_vector(
            _SCENARIO,
            tags=record.get("tags"),
            avg_cost=record.get("avg_cost"),
            category=record.get("category"),
        )
        content = {k: v for k, v in record.items() if k != "id"}
        store.add_post(record["id"], vector, content)
    return len(records)


if __name__ == "__main__":
    count = import_posts()
    print(f"已导入 {count} 条港澳达人帖子进 Chroma")

    print("--- 自测：查一个偏好粤菜/本地老店的人格，验证本地人认证帖子会被优先加权 ---")
    demo_persona = persona.bootstrap_from_onboarding(
        "hk-macau-demo",
        _SCENARIO,
        {
            "pace_score": 0.4,
            "budget_score": 0.3,
            "social_mode": "solo",
            "interest_theme": ["美食探店", "人文历史"],
            "taste": ["粤菜"],
            "novelty_score": 0.6,
        },
    )
    vector = persona.compute_persona_vector(demo_persona, _SCENARIO)
    results = store.query_similar_posts(vector, top_k=5)
    for r in results:
        print(f"  {r.get('post_id')} {r.get('place')} verified_local={r.get('verified_local')} score={r['similarity_score']:.3f}")
