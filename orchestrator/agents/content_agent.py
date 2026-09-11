"""
达人/内容 Agent -- 提出候选景点/美食供编排 Agent 筛选，不管排时间（排时间是
route_agent.schedule() 的事）。

两种场景共用同一条"查真实周边 POI + 社区口碑复核"流水线（2026-09-14 重构，`nearby` 从
独立 workflow 并入这里，见 docs/orchestrator-guide.md）：
- mode="trip"（默认，3日游/常规推荐）：先按人格向量从社区帖子库筛几个种子景点/美食，
  再对每个种子景点各自为中心分别查一次真实周边 POI，汇总去重
- mode="nearby"（周边游）：跳过人格检索种子这一步，LLM 从消息里解析出起点/游览小时数，
  直接以这个起点为中心查一次

两种场景查回来的真实 POI 都会过"口碑复核"（_nearby_plan() 内部）：查社区库有没有人评价过
这个地点，评价人的人格向量跟当前用户比对，决定排序权重——没有评价不等于排除，只是排后面。

模型档位：MODEL_FULL（UGC 语义提炼）。mode="nearby" 时会有一次 MODEL_LIGHT 调用解析请求
参数（复用原 nearby_planner.py 的思路），mode="trip" 目前还是纯向量检索，没有实际用到 LLM。
"""

import json
import sys
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

import llm_tool
import nearby_sources
import persona
import store

_KNOWN_CITIES = ("澳门", "香港")
_NEARBY_RADIUS_M = 1500  # 跟 nearby_sources.nearby() 默认半径一致，显式写出来方便以后调


def _extract_city(location_hint: str | None) -> str | None:
    """跟 orchestrator_agent._extract_city 同一个"先跑起来，以后再换实体识别"的子串匹配思路，
    独立一份没有互相 import，两个模块本来就不该耦合。"""
    if not location_hint:
        return None
    for city in _KNOWN_CITIES:
        if city in location_hint:
            return city
    return None


def _dedupe_by_place(posts: list[dict]) -> list[dict]:
    """同一个地点如果有好几条社区帖子都在夸，只留相似度最高的那条——posts 传进来时已经按
    similarity_score 降序排好（store.query_similar_posts() 保证的），遇到的第一条就是最高分
    那条，后面同地点的直接跳过。不去重的话，两条不同的人都写过"叠记咖喱美食"的帖子会各自占
    一个种子名额，变成同一家店被推荐两次（真实测过会出现这种情况）。没有 place 字段的帖子
    （理论上不该出现）原样保留，不参与去重。"""
    seen: set[str] = set()
    deduped = []
    for post in posts:
        place = post.get("place")
        if place and place in seen:
            continue
        if place:
            seen.add(place)
        deduped.append(post)
    return deduped


def _pick_seeds_with_category_balance(posts: list[dict], top_k: int) -> list[dict]:
    """挑种子时优先保证至少 1 条"景点"类，剩下按相似度顺序（posts 已经排好序）补满 top_k。

    不这么做的话，骨架式排班（route_agent.schedule() mode="trip"）的景点槽位可能天生没东西
    填——人格向量偏"美食探店"这类标签时，纯相似度 top_k 经常清一色是"饮食"类帖子，每个
    美食种子再去查周边真实 POI，一家餐厅周边搜出来的大概率还是餐厅，偏差会被放大（真实
    demo 演示时出现过"推荐的3天行程每天都是美食"）。"景点"类候选不够就照常按相似度顺序
    回退，不强求，跟"过滤完一个不剩就退回未过滤结果"是同一个"尽力而为，不硬凑"的哲学。"""
    sight = next((p for p in posts if p.get("category") == "景点"), None)
    if sight is None:
        return posts[:top_k]
    rest = [p for p in posts if p is not sight][: top_k - 1]
    return [sight] + rest


def _nearby_plan(origin: dict, city: str, persona_vector: list[float] | None) -> list[dict]:
    """
    统一的"周边探索"接口：查真实高德周边 POI + 社区口碑复核，返回排好序的候选列表。
    origin: {"lng":, "lat":, "name":, "id":}——3 日游场景是某个种子景点/帖子地点，周边游
      场景是用户给的起点。"id" 只用来给 nearby_sources.nearby() 排除起点自身，没有真实
      POI id 就传 None（geocode 出来的坐标没有 AMap POI id，属于正常情况）。
    persona_vector: 当前用户人格向量，跟"评价过这个地点的帖子"的人格向量做余弦相似度，
      决定口碑复核的权重；两种场景现在都会传。

    没有评价 ≠ 排除：229 条帖子覆盖的地点有限，大多数真实 POI 大概率查不到匹配的评价——
    排序是"有人格匹配的评价排最前，有评价但没算出匹配分的其次，完全没评价的排最后但仍保留"。
    地点匹配是精确字符串匹配（POI 名字 vs 帖子 place 字段），命中率本来就不高，是已知局限，
    符合项目一贯"先跑起来，以后再换更精细的匹配"的做法。
    查询失败（key 没配/网络问题）优雅降级成空列表，不抛异常炸穿调用方。
    """
    try:
        pois = nearby_sources.nearby(city, origin, radius=_NEARBY_RADIUS_M)
    except Exception:
        return []

    results = []
    for poi in pois:
        reviews = store.find_posts_by_place(poi["name"])
        match_score = None
        if reviews and persona_vector is not None:
            scored = [
                persona.cosine_similarity(persona_vector, r["persona_vector"])
                for r in reviews
                if r.get("persona_vector")
            ]
            if scored:
                match_score = max(scored)
        results.append(
            {
                "place": poi["name"],
                "city": city,
                "category": poi.get("category"),
                "address": poi.get("address"),
                "lng": poi.get("lng"),
                "lat": poi.get("lat"),
                "source": "高德POI",
                "community_reviews": reviews,
                "persona_match_score": match_score,
            }
        )

    # 有人格匹配分的排最前（分数越高越前）；有评价但算不出匹配分的其次；完全没评价的排最后但仍保留
    results.sort(key=lambda r: (r["persona_match_score"] is None, -(r["persona_match_score"] or 0)))
    return results


_NEARBY_PARSE_PROMPT = """将港澳周边游需求转成 JSON，字段：city(澳门/香港，缺省沿用上下文城市)、
origin(起点地标/地址文字)、hours(1到10的数字，缺省3)。只更新用户明确提到的字段。
缺少起点地标，回传 {"question":"需要追问的问题"}。只输出 JSON，不要输出其他文字。"""


def _parse_nearby_request(location_hint: str, default_city: str | None) -> dict:
    """mode="nearby" 用：LLM 把用户这句话解析成 {city, origin, hours} 或 {"question": ...}。
    复用原 nearby_planner.from_chat() 的思路，缩小范围只提取排时间不需要的字段
    （members/date/mode 这些跟"提出候选"无关，留给 route_agent.schedule() 处理）。"""
    raw = llm_tool.call_llm(
        [
            {"role": "system", "content": _NEARBY_PARSE_PROMPT},
            {"role": "user", "content": json.dumps({"message": location_hint, "default_city": default_city}, ensure_ascii=False)},
        ],
        model=llm_tool.MODEL_LIGHT,
    )
    try:
        clean = raw.strip()
        if clean.startswith("```"):
            clean = "\n".join(clean.splitlines()[1:-1])
        parsed = json.loads(clean)
    except (ValueError, TypeError):
        return {"question": "没听清起点是哪里，能再说一下具体地标或地址吗？"}
    if not isinstance(parsed, dict):
        return {"question": "没听清起点是哪里，能再说一下具体地标或地址吗？"}
    return parsed


def run(shared_state: dict, location_hint: str, mode: str = "trip", top_k: int = 3) -> dict:
    """
    mode="trip"（默认）：人格检索种子景点/美食（跟以前逻辑一致）→ 对每个种子分别调
      _nearby_plan() 查真实周边 POI，汇总去重 → 合并成候选列表。
    mode="nearby"：跳过人格检索种子 → LLM 从 location_hint 解析出起点/游览小时数 → 高德查
      真实起点坐标 → 调一次 _nearby_plan()。解析不出起点会返回 clarification_needed，
      调用方（orchestrator_agent）要检查这个字段，直接把追问文字当回复返回，不硬凑候选。

    返回 {"recommendations": [...候选，city/category/place/source(社区帖子|高德POI)/
      persona_match_score...], "nearby_params": {"origin":{lng,lat,name}, "hours":int} | None,
      "clarification_needed": str | None}
    """
    persona_vector = persona.compute_persona_vector(shared_state["persona"], shared_state["scenario"])
    city = _extract_city(location_hint) or shared_state.get("city")

    if mode == "nearby":
        parsed = _parse_nearby_request(location_hint, city)
        if "question" in parsed:
            return {"recommendations": [], "nearby_params": None, "clarification_needed": parsed["question"]}
        city = parsed.get("city") or city or "澳门"
        hours = parsed.get("hours") or 3
        try:
            origin = nearby_sources.find_origin(city, parsed["origin"])
        except Exception:
            return {
                "recommendations": [],
                "nearby_params": None,
                "clarification_needed": f"没查到「{parsed.get('origin')}」这个地方，能换个更具体的地标或地址吗？",
            }
        nearby_candidates = _nearby_plan(origin, city, persona_vector)
        return {
            "recommendations": nearby_candidates,
            "nearby_params": {"origin": {"lng": origin["lng"], "lat": origin["lat"], "name": origin["name"]}, "hours": hours},
            "clarification_needed": None,
        }

    # mode="trip"：先人格检索种子，再对每个种子分别扩展周边真实候选。
    # 多捞一批（top_k*5）再过滤掉 Tips 截断到 top_k——达人 Agent 的定位是"提出候选景点/
    # 美食"，Tips 类帖子（提醒事项，不是真实地点）从一开始就不该占种子名额。
    # 兜底：空白人格（没走 onboarding，novelty/pace 都是中性默认值）实测会系统性地更接近
    # Tips 帖子（Tips 没什么强标签，向量天然更靠近原点），真实 demo 里 server.py 一直用带
    # 具体偏好的 onboarding 人格，碰不到这个边界情况，但达人 Agent 本身不该对着任何输入都
    # 可能空手而归——过滤完一个不剩，就退回未过滤结果，好歹给点东西，不摆烂。
    fetched = store.query_similar_posts(persona_vector, top_k=top_k * 5, city=city)
    deduped = _dedupe_by_place(fetched)
    filtered = [p for p in deduped if p.get("category") != "Tips"]
    seed_posts = _pick_seeds_with_category_balance(filtered or deduped, top_k)
    seed_recommendations = [
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
            "city": p.get("city"),
            "category": p.get("category"),
            "post_type": p.get("post_type"),
            "address": p.get("address"),
            "tags": p.get("tags", []),
            "verified_local": p.get("verified_local", False),
            "source": "社区帖子",
        }
        for p in seed_posts
    ]

    nearby_extra: list[dict] = []
    seen_places = {r["place"] for r in seed_recommendations if r.get("place")}
    for seed in seed_recommendations:
        # category=="Tips" 的帖子 place 字段存的是提醒标题（比如"不要乱闯红灯"），不是真实地点，
        # 地理编码会查到不相关的垃圾坐标（实测查到过跑去云南的"东北菜馆"）——只有 Food/Sight
        # 才拿去扩展周边候选
        if not seed.get("place") or not city or seed.get("category") == "Tips":
            continue
        try:
            # 用 nearby_sources.find_origin()（高德 place/text + citylimit）而不是 map_tool.geocode()
            # （高德 geocode/geo）——实测 geocode/geo 对"渔人码头"这类多地重名的地标，在港澳场景下
            # 经常查到内地同名地点（比如查出过河北秦皇岛的坐标），find_origin 走的是 POI 搜索+
            # citylimit 硬限定，可靠得多，这个 session 之前也一直用它做港澳地理编码
            origin = nearby_sources.find_origin(city, seed["place"])
        except Exception:
            continue
        # 顺手把坐标回填到种子候选本身——route_agent.schedule() 排路线需要每个候选的经纬度，
        # 不然它还得自己再查一次地理编码，这里已经查过了，别重复劳动
        seed["lng"], seed["lat"] = origin["lng"], origin["lat"]
        for candidate in _nearby_plan(origin, city, persona_vector):
            if candidate["place"] in seen_places:
                continue
            seen_places.add(candidate["place"])
            nearby_extra.append(candidate)

    return {"recommendations": seed_recommendations + nearby_extra, "nearby_params": None, "clarification_needed": None}


if __name__ == "__main__":
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
    demo_shared_state = {"persona": demo_persona, "scenario": "vacation", "city": "澳门"}

    print("--- mode=trip ---")
    trip_result = run(demo_shared_state, location_hint="澳门", mode="trip", top_k=2)
    print(json.dumps(trip_result, ensure_ascii=False, indent=2))

    print("--- mode=nearby ---")
    nearby_result = run(demo_shared_state, location_hint="从大三巴出发逛3小时", mode="nearby")
    print(json.dumps(nearby_result, ensure_ascii=False, indent=2))
