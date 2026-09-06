"""
五个 Agent 的定义（对应 agent-architecture.md 的 Agent 划分）：
  orchestrator_agent  -- 编排 Agent（星型架构中枢）
  content_agent       -- 达人/内容 Agent
  route_agent         -- 行程/路线 Agent
  ota_hotel_agent     -- OTA/酒店 Agent
  exception_agent     -- 异常应变 Agent

每个模块统一暴露一个 run(shared_state, ...) 函数（orchestrator_agent 额外暴露
new_shared_state/classify_intent/orchestrate）。谁负责哪个 Agent 就改自己那一个文件，
不用碰别人的。
"""
