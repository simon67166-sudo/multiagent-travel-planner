"""
通用 LLM 调用工具 -- 跟"编排"这件事本身无关的基础设施，5 个 Agent 里任何一个
要调用模型都用这个，不要各自再写一遍 client 初始化。

依赖：pip install openai python-dotenv
运行前准备：在 orchestrator/.env 里写一行 PARATERA_API_KEY=你的key
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

BASE_URL = "https://llmapi.paratera.com/v1"
# 两档模型对齐 agent-architecture.md 的"模型档位"设计，具体哪个 Agent 用哪档见各自文件顶部注释
MODEL_FULL = "DeepSeek-V4-Pro"
MODEL_LIGHT = "DeepSeek-V4-Flash"

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("PARATERA_API_KEY")
        if not api_key:
            raise RuntimeError("环境变量 PARATERA_API_KEY 未设置，先设置好 API Key 再运行。")
        _client = OpenAI(api_key=api_key, base_url=BASE_URL)
    return _client


def call_llm(messages: list[dict], model: str = MODEL_FULL, **kwargs) -> str:
    resp = _get_client().chat.completions.create(model=model, messages=messages, **kwargs)
    return resp.choices[0].message.content


if __name__ == "__main__":
    # 最小连通性测试：只验证 API Key / base_url / model 能不能跑通，不涉及任何 Agent 逻辑
    print("MODEL_FULL 回复:", call_llm([{"role": "user", "content": "用一句话介绍你自己"}]))
    print("MODEL_LIGHT 回复:", call_llm([{"role": "user", "content": "用一句话介绍你自己"}], model=MODEL_LIGHT))
