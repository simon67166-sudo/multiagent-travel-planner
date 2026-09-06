"""
OTA/酒店 Agent -- 查库存/比价。

模型档位：MODEL_LIGHT（结构化查库存比价，轻量模型）。

现状：还是纯占位假数据，没有真实的比价/查库存来源可接。真正确认预订后应该调
trip_plan.add_hotel(shared_state["trip_plan"], {...}) 落地，目前这个流程还没有触发点
（orchestrate() 里只调了"查候选"，没有"确认预订"这一步）。
"""

import sys
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))


def run(
    shared_state: dict, location: str | None, date_range: str | None = None, category: str | None = None
) -> dict:
    return {
        "candidates": [
            {
                "name": "占位酒店/票务 X",
                "price": 399,
                "inventory": 5,
                "cancel_policy": "24小时内免费取消",
                "rating": 4.5,
                "provider_type": "hotel",
            }
        ]
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run(shared_state={}, location="杭州"), ensure_ascii=False, indent=2))
