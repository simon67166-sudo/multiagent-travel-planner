"""
OTA/酒店 Agent -- 查库存/比价。

模型档位：MODEL_LIGHT（结构化查库存比价，轻量模型）。

现状：还是纯占位假数据，没有真实的比价/查库存来源可接。字段现在补全到
"确认预订后能直接落地"需要的程度（机票带 flight_no/from_/to/depart_time/arrive_time，
酒店带 address/check_in/check_out）——这样 server.py 的 POST /widget-response
收到用户选中的候选后，能直接拿这些字段调 trip_plan.add_flight()/add_hotel()，
不用现造字段。date_range 传了就用来填 check_in/check_out，格式 "开始日期~结束日期"。
"""

import sys
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))


def run(
    shared_state: dict, location: str | None, date_range: str | None = None, category: str | None = None
) -> dict:
    if date_range and "~" in date_range:
        check_in, check_out = date_range.split("~", 1)
    else:
        check_in, check_out = "2026-10-01", "2026-10-03"

    city = location or "杭州"

    return {
        "candidates": [
            {
                "name": "MU5137 上海虹桥→杭州萧山",
                "flight_no": "MU5137",
                "from_": "上海虹桥国际机场",
                "to": "杭州萧山国际机场",
                "depart_time": "08:00",
                "arrive_time": "09:10",
                "status": "on_time",
                "price": 553,
                "inventory": 12,
                "rating": 4.8,
                "provider_type": "flight",
            },
            {
                "name": "占位酒店/票务 X",
                "address": f"{city}市中心",
                "check_in": check_in,
                "check_out": check_out,
                "price": 399,
                "inventory": 5,
                "cancel_policy": "24小时内免费取消",
                "rating": 4.5,
                "provider_type": "hotel",
            },
        ]
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run(shared_state={}, location="杭州", date_range="2026-10-01~2026-10-03"), ensure_ascii=False, indent=2))
