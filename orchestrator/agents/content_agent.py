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
import re
import sys
from datetime import date
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
_NEARBY_PER_CALL_CAP = 8  # 每次 _nearby_plan() 调用最多留几个候选，见该函数文档字符串

# 2026-09-17 真实踩过的坑：高德周边查询查回来一个叫"公厕"的 POI，高德自己给的 type 字段是
# "风景名胜;风景名胜;风景名胜"（typecode 110200）——高德自己的数据就标错了类别，
# _classify_categories() 看着这个"风景名胜"字样，理所当然地把它归成了"景点"塞进候选池。
# 靠 LLM 分类环节纠正这种"上游数据源自己标错类别"的情况不现实（分类提示词能判断的是
# "这条候选属于哪个类别"，没法凭空判断"这条候选压根不该被推荐"）——真正可靠的信号是
# 地名本身：公厕/停车场这类公共设施，不管高德把它归到哪个类别，都不该出现在候选池里。
# 命中这些关键词的 POI 在查回来这一步就直接排除，不进候选池，不用等分类环节再去猜。
_NON_RECOMMENDABLE_PLACE_KEYWORDS = ("公厕", "洗手间", "卫生间", "停车场", "收费站", "公共电话亭")


def _extract_city(location_hint: str | None) -> str | None:
    """跟 orchestrator_agent._extract_city 同一个"先跑起来，以后再换实体识别"的子串匹配思路，
    独立一份没有互相 import，两个模块本来就不该耦合。"""
    if not location_hint:
        return None
    for city in _KNOWN_CITIES:
        if city in location_hint:
            return city
    return None


_DAY_COUNT_PATTERN = re.compile(r"(\d+)\s*(?:天|日游)")
# 只认"N天"/"N日游"，不认光秃秃的"N日"——中文日期从来是"M月D日"/"D号"，不会说"M月D天"，
# 唯独"日"这个字既能表示"天数"（"5日游"）又能表示"日期"（"9月15日"），真实测试就踩过这个
# 坑："9月15日到9月18日"里的"15日"被当成"提取到了15天"，比日期区间兜底更早匹配上，
# 直接把 day_count 算成 14（封顶值）——要求"日"后面紧跟"游"字才算数，日期用法就不会再
# 误命中这条规则，能安全地交给下面的 _DATE_RANGE_PATTERN 兜底
# "9.15号到9.18"/"9月15日到9月18日"/"9/15-9/18" 这种日期区间，没有显式"N天"字样时的兜底——
# 2026-09-17 真实测试发现"我想在9.15号到9.18从北京去澳门玩"（没说"3天"）会被
# _DAY_COUNT_PATTERN 漏掉，day_count 悄悄退到默认值 1，连带把 fetch 数量/硬性类别配额都
# 缩没了，社区库里本来就有限的几条"景点"帖子排名不够靠前就直接被挤出候选池，最后排出来的
# 候选全是餐饮，一个景点都没有——不是排班逻辑的锅，是这里漏判了
_DATE_RANGE_PATTERN = re.compile(r"(\d{1,2})[月./](\d{1,2})[日号]?\s*[到至\-~～]\s*(\d{1,2})[月./](\d{1,2})[日号]?")


def _extract_day_count(text: str | None) -> int:
    """从用户消息里粗略提取"几天"，提取不到默认 1 天，封顶 14 天——跟 orchestrator_agent.py
    之前删掉的同名函数逻辑一样（那边删掉是因为不再用来触发自动排班，这里搬回来是另一个
    用途：决定这次该按几天的量去捞候选/配硬性类别配额，见 _pick_seeds_with_quotas()）。
    跟排时间的触发点（用户从候选卡片选完确认）完全无关，两者管的是不同的事。

    两级提取，显式"N天"/"N日"优先：① "3日游"/"5天"这种显式天数直接读数字；② 没有显式天数
    才退回日期区间兜底（"9.15号到9.18"这种），按公历日期差算天数（同年同月粗算，不处理
    跨年/大小月边界之外的极端情况，够用为止）。两种都提取不到才是真的默认 1 天。

    2026-09-17 用户指出："9.15到9.18"是4天（15/16/17/18号，通常说"4天3晚"），不是3天——
    日期差（18-15=3）算的是晚数，天数要在这基础上 +1（首尾两天都要算进去）。"""
    if not text:
        return 1
    match = _DAY_COUNT_PATTERN.search(text)
    if match:
        return max(1, min(int(match.group(1)), 14))
    range_match = _DATE_RANGE_PATTERN.search(text)
    if range_match:
        month1, day1, month2, day2 = (int(g) for g in range_match.groups())
        try:
            day_diff = (date(2000, month2, day2) - date(2000, month1, day1)).days
        except ValueError:
            day_diff = 0
        if day_diff > 0:
            return max(1, min(day_diff + 1, 14))
    return 1


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


def _pick_seeds_with_quotas(posts: list[dict], day_count: int) -> list[dict]:
    """挑种子时按天数强制配额：景点 >= 2×天数，早餐 >= 天数，午晚餐 >= 2×天数——对应骨架
    排班（route_agent._SLOT_TEMPLATE）每天 2 个景点槽位（上午+下午/晚上其中一个大概率能
    排上）、1 顿早餐、2 顿不同的午晚餐（lunch+dinner 都从同一个池子选，要 2×天数 才够
    "天数天、每天两顿都不重复"，不然天数一多会开始撞同一家店）。

    posts 传进来时已经按相似度降序排好（_classify_categories() 处理过、category 已经是
    干净的"景点"/"早餐"/"午晚餐"三选一），每个类别内部取到的就是那个类别里相似度最高的
    几条。某个类别候选不够配额就照单全收，不强求，跟"过滤完一个不剩就退回未过滤结果"是
    同一个"尽力而为，不硬凑"的哲学——229 条社区帖子覆盖有限，不是每个城市/类别都凑得够，
    靠后面的周边真实 POI 扩展再补一些。"""
    sight = [p for p in posts if p.get("category") == "景点"][: 2 * day_count]
    breakfast = [p for p in posts if p.get("category") == "早餐"][:day_count]
    lunch_dinner = [p for p in posts if p.get("category") == "午晚餐"][: 2 * day_count]
    return sight + breakfast + lunch_dinner


def _nearby_plan(
    origin: dict, city: str, persona_vector: list[float] | None, limit: int = _NEARBY_PER_CALL_CAP
) -> list[dict]:
    """
    统一的"周边探索"接口：查真实高德周边 POI + 社区口碑复核，返回排好序、已截断的候选列表。
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

    limit：截断保留几个（默认 8）——原型 nearby_planner.py（fellow 分支，已删除，重构成
    这一版之前的实现）的做法是先用一次 LLM 调用把候选"从资料中选出最多 6 个地点"，再
    `pool = ranked[:8]` 硬截断成 8 个，之后才走贪心排线路。当时那个设计没有现成的排序分数，
    所以要靠一次额外 LLM 调用做筛选；现在 recommendation_score/persona_match_score 已经是
    结构化排序依据（口碑复核算出来的），直接按这个分数截断效果等价，还省了一次 LLM 调用——
    单次 nearby() 请求（AMap 同城 POI 检索，radius=1500m，多个 type code 一起查）经常一次
    就回几十条，run() 里 mode="trip" 场景对每个种子都各自调一次这个函数（3 天行程能有十几个
    种子），不截断的话汇总去重完still是几百条量级（真实测过 290 条），既拖慢下游两轮
    _classify_categories() 的 LLM 调用，也让 route_agent 排程序 JSON payload 臃肿。
    """
    try:
        pois = nearby_sources.nearby(city, origin, radius=_NEARBY_RADIUS_M)
    except Exception:
        return []

    results = []
    for poi in pois:
        if any(keyword in poi["name"] for keyword in _NON_RECOMMENDABLE_PLACE_KEYWORDS):
            continue
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
                # route_agent._llm_review_day() 用的统一打分字段，见 recommendation_score
                # 的说明；没有口碑复核数据（match_score is None）给个偏低的默认值，
                # 跟上面"没有评价排最后但仍保留"是同一个降级逻辑，不是排除，只是分低
                "recommendation_score": match_score if match_score is not None else 0.0,
            }
        )

    # 有人格匹配分的排最前（分数越高越前）；有评价但算不出匹配分的其次；完全没评价的排最后但仍保留
    results.sort(key=lambda r: (r["persona_match_score"] is None, -(r["persona_match_score"] or 0)))
    return results[:limit]


_CATEGORY_FIX_PROMPT = """你是达人 Agent 的分类质检员。下面是一批候选地点（JSON 数组，每项：
index/place/category/caption/address），你的任务是把每一条都归类成"景点"/"早餐"/"午晚餐"
三选一中的一个：
- "景点"：观光/游览/打卡地
- "早餐"：适合早上吃的餐饮（早餐店、粥店、茶餐厅早市这类）
- "午晚餐"：正餐/晚餐/宵夜这类餐饮，也是餐饮类默认归类（实在判断不出早餐还是午晚餐时选这个）

只要某一条的 category 字段不是精确等于"景点"、"早餐"、"午晚餐"这三个词之一（比如笼统的
"饮食"、或者高德地图给的原始分类"餐饮服务;中餐厅;中餐厅"这种），你就必须给出归类判断，
不能以"信息不足"为理由跳过——景点还是餐饮通常从地名/原分类就能看出来，餐饮类如果判断不出
早餐还是午晚餐，就归"午晚餐"。只有 category 已经精确等于这三个词之一、且你觉得没有错，
才不需要输出这一条。
只输出需要改正/需要归类的条目，格式 [{"index":,"category":"景点"或"早餐"或"午晚餐"}]；
如果全部已经是精确的三选一标签且没有错误，输出空数组 []。只输出 JSON，不要输出其他任何
文字。"""


def _classify_categories(candidates: list[dict]) -> None:
    """候选分类质检：统一判成"景点"/"早餐"/"午晚餐"三选一，不是"饮食"这种粗粒度二元标签
    ——route_agent.schedule() 的骨架排班需要精确区分早餐槽位和午晚餐槽位，不能只知道
    "是餐饮"。一批候选一次性打包成一次 MODEL_LIGHT 调用（不是逐条调用——候选池经常几十条，
    逐条调模型会让这个 demo 本来就不快的延迟更差，跟 route_agent._llm_review_day() 一天
    调一次是同一个"批量、不逐项"的思路）。可以对不同批次的候选分别调用（种子候选池、周边
    扩展候选池不需要一次性传全部，见 run() 的调用方式）。

    社区帖子的 category 是人工录入的，可能真的标错或者只有粗粒度的"饮食"没细分早中晚；
    高德 POI 的 category 是原始 type 字符串，本身就不是干净的标签——标错/标粗一条，
    route_agent._pool_for_candidate() 分槽位池子就会错，骨架排班"每天保证早中晚配额"这个
    结构性保证就被数据质量问题绕过去了。

    提示词措辞很关键：真实测过"如果实在看不出来就保留原分类"这种宽松措辞会让模型偷懒，
    一批 34 条真实候选里 15 条原样保留成笼统的"饮食"，没有细分早中晚——现在的提示词改成
    "只要不是精确的三选一标签就必须归类，餐饮类判断不出早晚就默认午晚餐"，同一批数据全部
    34 条都归类干净了。

    原地修改 candidates 里每个 dict 的 category 字段，不返回新列表。带 post_id 的
    （社区帖子来源）改对了顺手写回数据库（store.update_post_category()）——高德 POI 没有
    持久化来源，只改这次返回结果里的内存字段。LLM 调用/解析失败就什么都不改，不影响主
    流程——这是质检，不是必需步骤。"""
    if not candidates:
        return
    payload = [
        {"index": i, "place": r.get("place"), "category": r.get("category"), "caption": r.get("caption"), "address": r.get("address")}
        for i, r in enumerate(candidates)
    ]
    try:
        raw = llm_tool.call_llm(
            [
                {"role": "system", "content": _CATEGORY_FIX_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            model=llm_tool.MODEL_LIGHT,
        )
        clean = raw.strip()
        if clean.startswith("```"):
            clean = "\n".join(clean.splitlines()[1:-1])
        fixes = json.loads(clean)
        if not isinstance(fixes, list):
            return
    except Exception:
        return

    for fix in fixes:
        if not isinstance(fix, dict):
            continue
        idx, new_category = fix.get("index"), fix.get("category")
        if not isinstance(idx, int) or idx not in range(len(candidates)) or new_category not in ("景点", "早餐", "午晚餐"):
            continue
        candidate = candidates[idx]
        if candidate.get("category") == new_category:
            continue
        candidate["category"] = new_category
        post_id = candidate.get("post_id")
        if post_id:
            try:
                store.update_post_category(post_id, new_category)
            except Exception:
                pass  # 数据库写回失败不影响这次返回结果里已经改对的内存字段


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


def run(shared_state: dict, location_hint: str, mode: str = "trip") -> dict:
    """
    mode="trip"（默认）：人格检索种子景点/美食（跟以前逻辑一致）→ 对每个种子分别调
      _nearby_plan() 查真实周边 POI，汇总去重 → 合并成候选列表。种子挑选按 location_hint
      里提取到的天数强制配额（_pick_seeds_with_quotas()），不是固定 top_k。
    mode="nearby"：跳过人格检索种子 → LLM 从 location_hint 解析出起点/游览小时数 → 高德查
      真实起点坐标 → 调一次 _nearby_plan()。解析不出起点会返回 clarification_needed，
      调用方（orchestrator_agent）要检查这个字段，直接把追问文字当回复返回，不硬凑候选。

    返回 {"recommendations": [...候选，city/category(景点|早餐|午晚餐)/place/
      source(社区帖子|高德POI)/recommendation_score...], "nearby_params":
      {"origin":{lng,lat,name}, "hours":int} | None, "clarification_needed": str | None,
      "day_count": int}——day_count 是从 location_hint 提取到的天数（mode="nearby"
      固定给 1，"周边逛几小时"这种场景本来就不是多天行程，_extract_day_count() 没意义）。
      2026-09-17 起编排 Agent 把这个值存进 shared_state，attraction_picker 确认时一次性
      排够这么多天（route_agent.schedule(time_budget_days=day_count)），不用每天单独
      确认一轮才能凑够多天行程。
    """
    persona_vector = persona.compute_persona_vector(shared_state["persona"], shared_state["scenario"])
    city = _extract_city(location_hint) or shared_state.get("city")

    if mode == "nearby":
        parsed = _parse_nearby_request(location_hint, city)
        if "question" in parsed:
            return {"recommendations": [], "nearby_params": None, "clarification_needed": parsed["question"], "day_count": 1}
        city = parsed.get("city") or city or "澳门"
        hours = parsed.get("hours") or 3
        try:
            origin = nearby_sources.find_origin(city, parsed["origin"])
        except Exception:
            return {
                "recommendations": [],
                "nearby_params": None,
                "clarification_needed": f"没查到「{parsed.get('origin')}」这个地方，能换个更具体的地标或地址吗？",
                "day_count": 1,
            }
        nearby_candidates = _nearby_plan(origin, city, persona_vector)
        _classify_categories(nearby_candidates)
        return {
            "recommendations": nearby_candidates,
            "nearby_params": {"origin": {"lng": origin["lng"], "lat": origin["lat"], "name": origin["name"]}, "hours": hours},
            "clarification_needed": None,
            "day_count": 1,
        }

    # mode="trip"：先人格检索种子，再对每个种子分别扩展周边真实候选。
    # 天数决定这次该捞多少候选：景点/早餐/午晚餐三类硬性配额（2×天数/天数/2×天数，见
    # _pick_seeds_with_quotas()），Chroma 查询是本地向量检索，不是真实网络调用，捞多一点
    # 不心疼——按天数放大过一遍，比固定量更贴合"天数越多要保证的量越大"这个真实需求。
    # 兜底：空白人格（没走 onboarding，novelty/pace 都是中性默认值）实测会系统性地更接近
    # Tips 帖子（Tips 没什么强标签，向量天然更靠近原点），真实 demo 里 server.py 一直用带
    # 具体偏好的 onboarding 人格，碰不到这个边界情况，但达人 Agent 本身不该对着任何输入都
    # 可能空手而归——过滤完一个不剩，就退回未过滤结果，好歹给点东西，不摆烂。
    day_count = _extract_day_count(location_hint)
    fetched = store.query_similar_posts(persona_vector, top_k=min(20 * day_count, 150), city=city)
    deduped = _dedupe_by_place(fetched)
    filtered = [p for p in deduped if p.get("category") != "Tips"]
    pool = filtered or deduped
    _classify_categories(pool)  # 配额筛选要靠干净的"景点"/"早餐"/"午晚餐"三选一标签，先分类
    seed_posts = _pick_seeds_with_quotas(pool, day_count)
    seed_recommendations = [
        {
            "post_id": p.get("post_id"),
            "place": p.get("place"),
            "time_slot": p.get("time_slot"),
            "avg_cost": p.get("avg_cost"),
            "rating": p.get("rating"),
            "similarity_score": p.get("similarity_score"),
            "recommendation_score": p.get("similarity_score"),
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

    _classify_categories(nearby_extra)  # 种子已经分类过了，这里只分类新出现的高德 POI 候选，不重复分类
    recommendations = seed_recommendations + nearby_extra
    return {"recommendations": recommendations, "nearby_params": None, "clarification_needed": None, "day_count": day_count}


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
    trip_result = run(demo_shared_state, location_hint="推荐一个2天的行程", mode="trip")
    print(json.dumps(trip_result, ensure_ascii=False, indent=2))

    print("--- mode=nearby ---")
    nearby_result = run(demo_shared_state, location_hint="从大三巴出发逛3小时", mode="nearby")
    print(json.dumps(nearby_result, ensure_ascii=False, indent=2))
