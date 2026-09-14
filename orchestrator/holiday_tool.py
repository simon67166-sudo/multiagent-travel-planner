"""
节假日日期工具 -- 用真实农历库算法定日期，不靠 LLM 自己编。

2026-09-17 真实踩过的坑：编排 Agent 自由组句回复里说"2026年中秋节是9/17"，是模型凭
训练数据editorializing出来的，不是真查出来的——农历转公历这种事对 LLM 来说本来就容易编错
（用 zhdate 算出来的真实日期是 2026-09-25，差了整整 8 天）。跟这个项目一贯"真实数据不瞎编"
的思路一致（真实天气/真实酒店/真实路线），节假日日期也该是查出来的事实，不是模型自己猜的。

依赖：pip install zhdate（纯本地农历<->公历换算，不需要联网/API key，比接一个真实的联网
搜索工具轻量得多——这类"哪天是哪个节日"的问题本身就是可以精确计算的，不需要联网搜索）。
"""

from zhdate import ZhDate

# 农历固定日期的节日：{节日名: (农历月, 农历日)}。清明节是节气不是固定农历日期，这里没收，
# 真要支持得接节气算法，先跑起来，以后再补。
_LUNAR_HOLIDAYS = {
    "春节": (1, 1),
    "元宵节": (1, 15),
    "端午节": (5, 5),
    "七夕节": (7, 7),
    "中秋节": (8, 15),
    "重阳节": (9, 9),
    "腊八节": (12, 8),
}

# 公历固定日期的节日：{节日名: (公历月, 公历日)}
_SOLAR_HOLIDAYS = {
    "元旦": (1, 1),
    "国庆节": (10, 1),
    "劳动节": (5, 1),
}


def get_holiday_date(holiday_name: str, year: int) -> str | None:
    """算某年某个节日的公历日期（"YYYY-MM-DD"），认不出这个节日名就返回 None，调用方
    不强求——不是所有节日都收在这两张表里，没收录的交给 LLM 自己说，不在这里硬凑。"""
    if holiday_name in _SOLAR_HOLIDAYS:
        month, day = _SOLAR_HOLIDAYS[holiday_name]
        from datetime import date
        return date(year, month, day).isoformat()
    if holiday_name in _LUNAR_HOLIDAYS:
        month, day = _LUNAR_HOLIDAYS[holiday_name]
        try:
            return ZhDate(year, month, day).to_datetime().date().isoformat()
        except ValueError:
            return None
    return None


def find_mentioned_holidays(text: str, year: int) -> dict[str, str]:
    """text 里提到了哪些认识的节日名，就返回 {节日名: 真实日期} ——orchestrator_agent.py
    组句回复之前，把这个塞进给 LLM 的资料里，让模型如实转述查出来的日期，不用自己编。
    子串匹配，跟这个项目其它"先跑起来"的实体识别（_extract_city() 这类）是同一个思路。"""
    all_names = list(_LUNAR_HOLIDAYS) + list(_SOLAR_HOLIDAYS)
    found = {}
    for name in all_names:
        if name in text:
            resolved = get_holiday_date(name, year)
            if resolved:
                found[name] = resolved
    return found


if __name__ == "__main__":
    print("2026年中秋节:", get_holiday_date("中秋节", 2026))
    print("2026年春节:", get_holiday_date("春节", 2026))
    print("2026年国庆节:", get_holiday_date("国庆节", 2026))
    print(find_mentioned_holidays("中秋节想去澳门玩，国庆节要不要也安排一下", 2026))
