"""
trip_preferences.py -- 描述一次具体行程里，persona 系统没有覆盖的偏好维度：酒店偏好、
机票优先级。

背景：用户希望编排 Agent 在真正调用达人 Agent 搜索之前，先跟用户确认清楚旅行模式、酒店
需求、机票倾向、旅行节奏，做到更精准的推荐；并问能不能跟 persona 系统交叉引用。查过
persona.py 之后确认："旅行节奏"（pace_score，0=完全休闲/1=完全特种兵）、"度假 vs 出差"
（scenario 顶层字段）、"逛吃美食 vs 景点打卡"（interest_theme 标签，已有"美食探店"/
"自然风光"/"人文历史"这些词）这三个维度已经在 persona 系统里，不需要重新问、更不需要
在这里重复存一份——两个地方存着同一个概念，改一个忘了改另一个，是真实的数据不一致风险。

这个文件只存 persona 真正没有的两个新维度：
- hotel_preference："周边"（住行程附近图方便）| "度假酒店"（单独挑一家好酒店当度假体验，
  不介意离行程远）——这不等于 budget_score，预算不高的人也可能想为住宿单独多花钱。
- flight_priority："省钱" | "折衷" | "极致体验"——budget_score 能大致反推两端，但
  "时间不敏感"这种中间态不完全等价，而且机票是独立的一次性决策，不像酒店风格那样跟长期
  人格强相关。

describe_for_persona() 负责把"节奏"/"模式"这两个已经在 persona 里的维度读出来，跟这两个
新字段拼一起给调用方（比如以后想让 content_agent.py 也读一份"这次旅行整体偏好"）用，调用方
不需要知道这两类字段分别存在哪里。

模型档位：不需要 LLM——纯数据结构。解析用户对追问的自由文本回答是 orchestrator_agent.py
的事（_parse_preference_answer()），这个文件只管"存什么、怎么判断完整、缺了怎么兜底"。
"""

from dataclasses import dataclass

HOTEL_PREFERENCES = ("周边", "度假酒店")
FLIGHT_PRIORITIES = ("省钱", "折衷", "极致体验")

_DEFAULT_HOTEL_PREFERENCE = "周边"  # 用户说"继续"/不回答时的默认值，等价于改动前的锚点搜索行为
_DEFAULT_FLIGHT_PRIORITY = "折衷"


@dataclass
class TripPreferences:
    hotel_preference: str | None = None
    flight_priority: str | None = None

    def missing_fields(self) -> list[str]:
        """还没问出来的字段，给 orchestrator_agent.py 拼追问文案用。"""
        missing = []
        if self.hotel_preference is None:
            missing.append("hotel_preference")
        if self.flight_priority is None:
            missing.append("flight_priority")
        return missing

    def is_complete(self) -> bool:
        return not self.missing_fields()

    def apply_defaults(self) -> None:
        """用户明确表示"继续"/跳过、或者解析不出回答时调用——缺的字段填中性默认值，
        不会因为解析失败就一直卡在追问这一步，也不会反复追问第二次。"""
        if self.hotel_preference is None:
            self.hotel_preference = _DEFAULT_HOTEL_PREFERENCE
        if self.flight_priority is None:
            self.flight_priority = _DEFAULT_FLIGHT_PRIORITY

    def to_dict(self) -> dict:
        return {"hotel_preference": self.hotel_preference, "flight_priority": self.flight_priority}

    @classmethod
    def from_dict(cls, data: dict | None) -> "TripPreferences":
        """值不在合法枚举里（比如脏数据/以后改了词表）就当没填，不强行接受非法值。"""
        if not data:
            return cls()
        hotel_preference = data.get("hotel_preference")
        flight_priority = data.get("flight_priority")
        return cls(
            hotel_preference=hotel_preference if hotel_preference in HOTEL_PREFERENCES else None,
            flight_priority=flight_priority if flight_priority in FLIGHT_PRIORITIES else None,
        )


def describe_for_persona(persona_obj: dict, scenario: str) -> dict:
    """从 persona 系统读"旅行节奏"（pace_score）/"旅行模式"（scenario + interest_theme
    标签）——这两个维度已经在 persona 里，不在 TripPreferences 里重复存。调用方要看
    "这次旅行整体偏好"就调这个函数，不用自己知道该去 persona 的哪个字段翻。"""
    import persona as persona_module

    traits = persona_module.get_scenario_traits(persona_obj, scenario)
    return {
        "pace_score": traits.get("pace_score", 0.5),
        "scenario": scenario,
        "interest_theme": traits.get("interest_theme", []),
    }


if __name__ == "__main__":
    prefs = TripPreferences()
    print("初始缺失字段:", prefs.missing_fields())
    prefs.hotel_preference = "度假酒店"
    print("填了酒店偏好之后:", prefs.missing_fields(), "完整吗:", prefs.is_complete())
    prefs.apply_defaults()
    print("应用默认值之后:", prefs.to_dict(), "完整吗:", prefs.is_complete())

    restored = TripPreferences.from_dict({"hotel_preference": "不存在的值", "flight_priority": "省钱"})
    print("非法值兜底成 None，合法值保留:", restored.to_dict())
