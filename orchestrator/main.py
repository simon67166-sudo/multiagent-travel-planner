"""
入口脚本 -- 组装 shared_state、跑一次完整的端到端 demo。

真正的 Agent 逻辑都在 agents/ 目录下（编排 Agent + 4 个子 Agent，各自一个文件），
通用 LLM 调用工具在 llm_tool.py。这个文件只是胶水代码：
- re-export agents.orchestrator_agent 的 new_shared_state/orchestrate 给 server.py 用
  （server.py 完全不用改，还是 import main 调这两个函数）
- __main__ 里跑一次完整演示：推荐 → 排路线 → 异常应变 → 打印最终行程

运行前准备：
- 在 orchestrator/.env 里写一行 PARATERA_API_KEY=你的key （这个文件已加进 .gitignore，不会被提交）
- pip install openai python-dotenv chromadb
"""

import json

import persona
import store
import trip_plan
from agents.orchestrator_agent import new_shared_state, orchestrate  # noqa: F401  (re-export 给 server.py 用)

if __name__ == "__main__":
    # 先造 demo 用户的 persona，再往社区库里塞一条"跟他很像的人"发的帖子，
    # 这样 content_agent 才有真实候选可查（不是空的）。
    onboarding_answers = {
        "pace_score": 0.2,
        "budget_score": 0.5,
        "social_mode": "family",
        "interest_theme": ["自然风光", "美食探店"],
        "taste": ["江浙菜"],
        "novelty_score": 0.3,
    }
    state = new_shared_state("demo-user", scenario="vacation", onboarding_answers=onboarding_answers)

    demo_persona = persona.bootstrap_from_onboarding("demo-neighbor", "vacation", onboarding_answers)
    demo_vector = persona.compute_persona_vector(demo_persona, "vacation")
    store.add_post(
        "demo-seed-post",
        demo_vector,
        {
            "place": "西湖",
            "time_slot": "上午",
            "avg_cost": 120,
            "rating": 4.7,
            "avoid_tips": "旺季排队较久",
            "verified_trip": True,
            "caption": "和你人格很像的一位游客发的帖子",
        },
    )

    print("=== 第一轮：推荐 + 规划路线 ===")
    message = "帮我推荐一下杭州适合玩的地方，顺便排一下路线"
    output, state = orchestrate(message, state)
    print(json.dumps(output, ensure_ascii=False, indent=2))

    print("\n=== 第二轮：异常应变（西湖临时封闭）===")
    message2 = "刚看到通知，西湖今天临时封闭施工了"
    output2, state = orchestrate(message2, state)
    print(json.dumps(output2, ensure_ascii=False, indent=2))

    print("\n=== 当前完整行程（trip_plan.render）===")
    print(json.dumps(trip_plan.render(state["trip_plan"]), ensure_ascii=False, indent=2))
