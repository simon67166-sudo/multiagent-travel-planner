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
        _llm_select(candidates, "place", top_k, "选出最值得展示给用户的帖子")
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

    用 "place" 当 _llm_select 的校验 key，不是 "post_id"——2026-09-14 起 content_agent.run()
    的候选有两种来源（社区帖子带 post_id，高德 POI 不带），"place" 是两种来源都有的字段，
    换成它才不会让 POI 来源的候选在 LLM 精选这一步被静默漏掉。
    """
    pool = (
        _llm_select(candidates, "place", pool_size, "选出最值得推荐用户挑选的景点")
        if use_llm_rerank
        else candidates[:pool_size]
    )
    return {"widget": "attraction_picker", "data": {"options": pool, "max_select": max_select}}


def _filter_by_provider(candidates: list[dict], provider_type: str) -> list[dict]:
    """ota_hotel_agent 的 candidates 用 provider_type 字段区分机票/酒店，两个 widget 各自只挑自己那部分。"""
    return [c for c in candidates if c.get("provider_type") == provider_type]


def build_flight_picker_widget(
    candidates: list[dict], max_select: int = 1, pool_size: int = 3, use_llm_rerank: bool = True
) -> dict | None:
    """
    机票选择插件：跟 build_attraction_picker_widget 同一套"可选池 + 前端勾选 + 结果回传"契约，
    只是机票通常只订一个，默认 max_select=1（传大于 1 可以支持"多程/多人分开订"这种以后再扩展的场景）。
    candidates 通常是 ota_hotel_agent.run() 的 candidates，本函数只挑 provider_type == "flight" 的。
    候选里没有独立 id 字段，用 name 当 _llm_select 的校验 key（跟 post_id 的作用一样，防止 LLM 编造不存在的选项）。
    候选为空（比如这轮没查到机票）就返回 None，调用方按 None 跳过，不展示空插件。
    用户选完之后怎么回传触发"确认预订"，留给 POST /widget-response 接口，跟 attraction_picker 一起做。
    """
    flights = _filter_by_provider(candidates, "flight")
    if not flights:
        return None
    pool = (
        _llm_select(flights, "name", pool_size, "综合价格和退改政策，选出最值得推荐用户挑选的机票选项")
        if use_llm_rerank
        else flights[:pool_size]
    )
    return {"widget": "flight_picker", "data": {"options": pool, "max_select": max_select}}


def build_hotel_picker_widget(
    candidates: list[dict], max_select: int = 1, pool_size: int = 3, use_llm_rerank: bool = True
) -> dict | None:
    """
    酒店选择插件：跟 build_flight_picker_widget 同一套契约，默认 max_select=1（通常只订一家）。
    candidates 只挑 provider_type == "hotel" 的；候选为空就返回 None。
    """
    hotels = _filter_by_provider(candidates, "hotel")
    if not hotels:
        return None
    pool = (
        _llm_select(hotels, "name", pool_size, "综合价格、评分和取消政策，选出最值得推荐用户挑选的酒店")
        if use_llm_rerank
        else hotels[:pool_size]
    )
    return {"widget": "hotel_picker", "data": {"options": pool, "max_select": max_select}}


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

    demo_booking_candidates = [
        {"name": "杭州东-北京南 G21", "price": 553, "inventory": 12, "rating": 4.8, "provider_type": "flight"},
        {"name": "杭州萧山-首都机场 MU5137", "price": 780, "inventory": 3, "rating": 4.2, "provider_type": "flight"},
        {"name": "西湖国宾馆", "price": 1280, "inventory": 2, "cancel_policy": "24小时内免费取消", "rating": 4.9, "provider_type": "hotel"},
        {"name": "如家·西湖店", "price": 320, "inventory": 8, "cancel_policy": "不可取消", "rating": 4.1, "provider_type": "hotel"},
    ]
    print(json.dumps(build_flight_picker_widget(demo_booking_candidates), ensure_ascii=False, indent=2))
    print(json.dumps(build_hotel_picker_widget(demo_booking_candidates), ensure_ascii=False, indent=2))
