"""
人格变量模块 -- 定义"旅游人格"的具体维度结构。

维度设计（这版把能量化的维度都改成了数值，而不是纯标签）：

  跨场景稳定的个人特质（stable_traits，不随本次旅行模式变化）：
    taste: list[str]              菜系标签，取值必须在 _TASTE_VOCAB 词表里（multi-hot 编码用）
    novelty_score: float 0~1      0=完全稳妥大众，1=完全小众冒险

  按"本次旅行模式"分场景存的特质（scenario_traits，键是 scenario 名，
  比如 business_trip/vacation/family/solo_adventure，同一个人不同场景下这些值可能完全相反）：
    pace_score: float 0~1          0=完全休闲，1=完全特种兵
    budget_score: float 0~1        0=完全穷游，1=公务舱/总统套房/租车这种享受型消费
                                    -- 硬过滤字段，用 budget_filter_ok() 按数值区间过滤，
                                       不参与相似度向量计算（避免把预算中等的用户匹配到
                                       消费不起的内容，见 agent-architecture.md 4.2）
    social_mode: str               "solo" | "couple" | "family" | "friends"
                                    -- 几种并列模式而非程度光谱，向量里按 one-hot 编码
    interest_theme: list[str]      取值必须在 _INTEREST_VOCAB 词表里（multi-hot 编码用）

  budget_score 为什么也量化但还是"硬过滤"：量化是为了能表达"小康和小康之间也有差异"
  这种细粒度区别（0.45 和 0.55 都是小康但不完全一样），但过滤逻辑上还是用数值区间匹配
  （budget_filter_ok），而不是把它塞进相似度向量参与排序 —— 数值化 ≠ 一定要参与向量相似度。

  _TASTE_VOCAB / _INTEREST_VOCAB 是固定词表，改动词表会改变向量维度，
  如果本地已经有存量数据（Chroma collection），改词表前记得清掉 orchestrator/data/chroma
  重新灌数据，不然新旧向量维度对不上。

不在本文件范围内（待定，留给以后 / 队友）：
  - compute_persona_vector() 现在是"数值特征拼接"，比上一版哈希字符串更有意义，
    但仍不是真实语义 embedding，以后要接真实 embedding 模型可以整体替换，
    调用方（store.py）的接口不用变。
  - 从一条用户反馈文本判断"该把哪个维度调成什么值/调多少"的算法：apply_feedback()
    只负责把调用方已经决定好的新值写进去、留痕，具体"怎么从反馈判断新值"留给
    编排/达人 Agent 决定。
"""

from typing import Any

_SCENARIO_SCORE_FIELDS = ("pace_score", "budget_score")
_SCENARIO_CATEGORICAL_FIELDS = ("social_mode",)
_SCENARIO_TAG_FIELDS = ("interest_theme",)
_SCENARIO_FIELDS = _SCENARIO_SCORE_FIELDS + _SCENARIO_CATEGORICAL_FIELDS + _SCENARIO_TAG_FIELDS

_STABLE_SCORE_FIELDS = ("novelty_score",)
_STABLE_TAG_FIELDS = ("taste",)
_STABLE_FIELDS = _STABLE_SCORE_FIELDS + _STABLE_TAG_FIELDS

_SOCIAL_MODES = ("solo", "couple", "family", "friends")
_TASTE_VOCAB = ("川菜", "粤菜", "江浙菜", "西北菜", "日料", "西餐", "清真", "海鲜")
_INTEREST_VOCAB = ("自然风光", "人文历史", "都市购物", "美食探店", "摄影打卡", "户外运动")


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _validate_tags(field: str, tags: list[str], vocab: tuple) -> None:
    unknown = set(tags) - set(vocab)
    if unknown:
        raise ValueError(f"{field} 里有不在词表内的标签: {unknown}，允许的词表是 {vocab}")


def new_persona(user_id: str) -> dict:
    return {
        "user_id": user_id,
        "stable_traits": {"taste": [], "novelty_score": 0.5},
        "scenario_traits": {},  # scenario -> {pace_score, budget_score, social_mode, interest_theme}
        "feedback_log": [],
    }


def set_scenario_traits(persona: dict, scenario: str, **fields: Any) -> None:
    unknown = set(fields) - set(_SCENARIO_FIELDS)
    if unknown:
        raise ValueError(f"未知的场景维度: {unknown}，只能是 {_SCENARIO_FIELDS}")

    if "social_mode" in fields and fields["social_mode"] not in _SOCIAL_MODES:
        raise ValueError(f"social_mode 只能是 {_SOCIAL_MODES}")
    if "interest_theme" in fields:
        _validate_tags("interest_theme", fields["interest_theme"], _INTEREST_VOCAB)
    for score_field in _SCENARIO_SCORE_FIELDS:
        if score_field in fields:
            fields[score_field] = _clamp01(fields[score_field])

    scenario_traits = persona["scenario_traits"].setdefault(scenario, {})
    scenario_traits.update(fields)


def get_scenario_traits(persona: dict, scenario: str) -> dict:
    return persona["scenario_traits"].get(scenario, {})


def update_stable_traits(persona: dict, **fields: Any) -> None:
    unknown = set(fields) - set(_STABLE_FIELDS)
    if unknown:
        raise ValueError(f"未知的稳定特质: {unknown}，只能是 {_STABLE_FIELDS}")

    if "taste" in fields:
        _validate_tags("taste", fields["taste"], _TASTE_VOCAB)
    if "novelty_score" in fields:
        fields["novelty_score"] = _clamp01(fields["novelty_score"])

    persona["stable_traits"].update(fields)


def apply_feedback(persona: dict, scenario: str, dimension: str, new_value: Any, reason: str = "") -> None:
    """
    按维度校准（agent-architecture.md 4.4 要求反馈粒度拆开，不能只打总分）。
    "该调成什么值"由调用方决定好再传进来，这里只负责写入 + 校验 + 留痕。
    """
    if dimension in _SCENARIO_FIELDS:
        set_scenario_traits(persona, scenario, **{dimension: new_value})
    elif dimension in _STABLE_FIELDS:
        update_stable_traits(persona, **{dimension: new_value})
    else:
        raise ValueError(f"未知维度: {dimension}")
    persona["feedback_log"].append(
        {"scenario": scenario, "dimension": dimension, "new_value": new_value, "reason": reason}
    )


def bootstrap_from_onboarding(user_id: str, scenario: str, answers: dict[str, Any]) -> dict:
    """冷启动用的轻量 onboarding（agent-architecture.md 五、风险点1）。示例见文件底部自测代码。"""
    persona = new_persona(user_id)
    update_stable_traits(
        persona,
        taste=answers.get("taste", []),
        novelty_score=answers.get("novelty_score", 0.5),
    )
    set_scenario_traits(
        persona,
        scenario,
        pace_score=answers.get("pace_score", 0.5),
        budget_score=answers.get("budget_score", 0.5),
        social_mode=answers.get("social_mode", "solo"),
        interest_theme=answers.get("interest_theme", []),
    )
    return persona


def budget_filter_ok(a_budget_score: float, b_budget_score: float, max_diff: float = 0.25) -> bool:
    """
    硬过滤用：判断两个 budget_score 是否"够接近"，接近才允许互相推荐/匹配。
    数值化不代表参与相似度向量排序，budget 用这个函数做区间过滤，不进 compute_persona_vector()。
    """
    return abs(a_budget_score - b_budget_score) <= max_diff


def compute_persona_vector(persona: dict, scenario: str) -> list[float]:
    """
    把数值特征拼接成向量：[novelty_score, taste multi-hot, pace_score, social_mode one-hot, interest_theme multi-hot]
    维度固定 = 1 + len(_TASTE_VOCAB) + 1 + len(_SOCIAL_MODES) + len(_INTEREST_VOCAB)。
    仍然是临时占位实现（不是真实语义 embedding），但比上一版哈希字符串有意义得多——
    分数越接近的人，向量距离真的越近。故意不编入 budget_score，理由见文件头部说明。
    """
    scenario_traits = get_scenario_traits(persona, scenario)

    vector = [persona["stable_traits"].get("novelty_score", 0.5)]

    taste = set(persona["stable_traits"].get("taste", []))
    vector.extend(1.0 if tag in taste else 0.0 for tag in _TASTE_VOCAB)

    vector.append(scenario_traits.get("pace_score", 0.5))

    social_mode = scenario_traits.get("social_mode")
    vector.extend(1.0 if mode == social_mode else 0.0 for mode in _SOCIAL_MODES)

    interests = set(scenario_traits.get("interest_theme", []))
    vector.extend(1.0 if tag in interests else 0.0 for tag in _INTEREST_VOCAB)

    return vector


if __name__ == "__main__":
    import json

    import store

    alice = bootstrap_from_onboarding(
        "alice",
        "vacation",
        {
            "pace_score": 0.15,
            "budget_score": 0.5,
            "social_mode": "family",
            "interest_theme": ["自然风光", "美食探店"],
            "taste": ["江浙菜"],
            "novelty_score": 0.2,
        },
    )
    bob = bootstrap_from_onboarding(
        "bob",
        "vacation",
        {
            "pace_score": 0.9,
            "budget_score": 0.1,
            "social_mode": "solo",
            "interest_theme": ["都市购物", "摄影打卡"],
            "taste": ["川菜"],
            "novelty_score": 0.85,
        },
    )
    # 和 alice 预算差不多、但节奏/兴趣不同的第三个人，验证 budget_filter_ok 独立于相似度向量
    carol = bootstrap_from_onboarding(
        "carol",
        "vacation",
        {
            "pace_score": 0.55,
            "budget_score": 0.55,
            "social_mode": "friends",
            "interest_theme": ["都市购物"],
            "taste": ["粤菜"],
            "novelty_score": 0.5,
        },
    )

    alice_vec = compute_persona_vector(alice, "vacation")
    bob_vec = compute_persona_vector(bob, "vacation")
    print("向量维度:", len(alice_vec))

    store.add_post(
        "post-alice-like",
        alice_vec,
        {"place": "乌镇", "time_slot": "上午", "avg_cost": 150, "rating": 4.6, "verified_trip": True},
    )
    store.add_post(
        "post-bob-like",
        bob_vec,
        {"place": "淮海路", "time_slot": "晚上", "avg_cost": 300, "rating": 4.3, "verified_trip": True},
    )

    print("--- alice 的人格向量查到的候选（应该是 post-alice-like 排前面）---")
    print(json.dumps(store.query_similar_posts(alice_vec, top_k=2), ensure_ascii=False, indent=2))

    print(
        "--- budget_filter_ok 验证：alice(0.5) vs carol(0.55) ->",
        budget_filter_ok(
            get_scenario_traits(alice, "vacation")["budget_score"],
            get_scenario_traits(carol, "vacation")["budget_score"],
        ),
        " | alice(0.5) vs bob(0.1) ->",
        budget_filter_ok(
            get_scenario_traits(alice, "vacation")["budget_score"],
            get_scenario_traits(bob, "vacation")["budget_score"],
        ),
    )

    print("--- 反馈闭环：alice 说这次是朋友一起去的，不是家庭 ---")
    apply_feedback(alice, "vacation", "social_mode", "friends", reason="这次是朋友一起去的，不是家庭")
    print(json.dumps(alice, ensure_ascii=False, indent=2))
