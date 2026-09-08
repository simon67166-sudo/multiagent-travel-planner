"""
展示插件（widgets）-- 右侧聊天框里的富交互小组件，跟 Agent/编排逻辑解耦。

设计原则：
- 候选集永远来自真实代码算出来的数据（比如 store.query_similar_posts() 经 content_agent
  处理后的 recommendations），保真，不允许凭空捏造。
- 如果要用 LLM 做"选择/排序"，LLM 只能从候选集里选 ID，不允许它自己编内容；
  代码会校验 LLM 返回的 ID 是否都在候选集里，筛掉任何编造的 ID，防止幻觉展示假数据。
  LLM 调用/解析失败时优雅降级成"按候选集原有顺序截取"，不影响整体流程。
- 每个 widget 统一格式：{"widget": "<type>", "data": {...}}，前端（web/index.html）
  按 widget 类型分发渲染，渲染逻辑完全不用管这些数据是怎么算出来的。

模型档位：_llm_select() 内部用 llm_tool.MODEL_LIGHT（选择/排序这种结构化任务，不需要强模型）。
"""

import json
import sys
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

import llm_tool


def _llm_select(candidates: list[dict], id_field: str, top_k: int, context: str) -> list[dict]:
    """
    用 LLM 从 candidates 里选出最多 top_k 个，只允许选候选集里真实存在的 ID，
    过滤掉任何编造的 ID。LLM 调用/解析失败时降级成"按原顺序截取前 top_k"。
    """
    by_id = {c[id_field]: c for c in candidates if c.get(id_field)}
    if not by_id:
        return candidates[:top_k]

    listing = "\n".join(f"{cid}: {json.dumps(c, ensure_ascii=False)}" for cid, c in by_id.items())
    prompt = (
        f"{context}，从下面的候选里选出最多 {top_k} 个，只能选列表里已有的 ID，不能编造新的。\n"
        f"只输出 JSON 数组，元素是选中的 ID 字符串，不要输出其他任何文字。\n\n候选列表：\n{listing}"
    )
    try:
        raw = llm_tool.call_llm([{"role": "user", "content": prompt}], model=llm_tool.MODEL_LIGHT)
        picked_ids = json.loads(raw.strip().strip("`"))
        selected = [by_id[pid] for pid in picked_ids if pid in by_id]  # 关键校验：只留真实存在的 ID
        if selected:
            return selected[:top_k]
    except Exception:
        pass
    return candidates[:top_k]  # 降级：LLM 失败/解析失败就按原顺序截取


def build_post_list_widget(candidates: list[dict], top_k: int = 3, use_llm_rerank: bool = False) -> dict:
    """
    帖子展示插件。candidates 通常是 content_agent.run() 的 recommendations（已按相似度排过序）。
    默认不额外调 LLM（candidates 已经是真实排序过的数据，直接截取展示就够了）；
    想要更智能的二次筛选/文案可以传 use_llm_rerank=True。
    """
    selected = (
        _llm_select(candidates, "post_id", top_k, "选出最值得展示给用户的帖子")
        if use_llm_rerank
        else candidates[:top_k]
    )
    return {"widget": "post_list", "data": {"posts": selected}}


def build_attraction_picker_widget(
    candidates: list[dict], max_select: int = 3, pool_size: int = 6, use_llm_rerank: bool = True
) -> dict:
    """
    景点选择插件：从候选里选出 pool_size 个放进"可选池"，前端展示给用户勾选最多 max_select 个。
    选择结果由前端回传给 server.py 的 POST /widget-response 接口，不在这个函数里处理。
    """
    pool = (
        _llm_select(candidates, "post_id", pool_size, "选出最值得推荐用户挑选的景点")
        if use_llm_rerank
        else candidates[:pool_size]
    )
    return {"widget": "attraction_picker", "data": {"options": pool, "max_select": max_select}}


if __name__ == "__main__":
    demo_candidates = [
        {"post_id": "p1", "place": "西湖", "rating": 4.7, "similarity_score": 0.95},
        {"post_id": "p2", "place": "灵隐寺", "rating": 4.5, "similarity_score": 0.8},
        {"post_id": "p3", "place": "河坊街", "rating": 4.2, "similarity_score": 0.6},
    ]
    print(json.dumps(build_post_list_widget(demo_candidates, top_k=2), ensure_ascii=False, indent=2))
    print(
        json.dumps(
            build_attraction_picker_widget(demo_candidates, max_select=2, pool_size=3),
            ensure_ascii=False,
            indent=2,
        )
    )
