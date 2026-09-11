"""
通用 LLM 调用工具 -- 跟"编排"这件事本身无关的基础设施，任何 Agent 要调用模型都用这个，
不要各自再写一遍 client 初始化。

依赖：pip install openai python-dotenv
运行前准备：在 orchestrator/.env（或仓库根目录 .env）里写一行 LLM_API_KEY=你的key
（兼容旧的 PARATERA_API_KEY 变量名，两个都设了优先用 LLM_API_KEY）。
BASE_URL/两档模型名也可以用 LLM_BASE_URL/LLM_MODEL_PRO/LLM_MODEL_FLASH 覆盖，不传就用默认值。
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

_ROOT = Path(__file__).resolve().parent.parent
# 先加载根目录 .env，再加载 orchestrator/.env——后面的会覆盖前面同名变量，
# 兼容"密钥可能放在仓库根目录，也可能放在 orchestrator 子目录"两种历史习惯
load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / "orchestrator" / ".env")

BASE_URL = os.getenv("LLM_BASE_URL", "https://llmapi.paratera.com/v1").strip()
# 两档模型对齐 agent-architecture.md 的"模型档位"设计，具体哪个 Agent 用哪档见各自文件顶部注释
MODEL_FULL = os.getenv("LLM_MODEL_PRO", "DeepSeek-V4-Pro-0813").strip()
MODEL_LIGHT = os.getenv("LLM_MODEL_FLASH", "DeepSeek-V4-Flash-0731").strip()

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        key = os.getenv("LLM_API_KEY") or os.getenv("PARATERA_API_KEY")
        if not key or not key.strip():
            raise RuntimeError("环境变量 LLM_API_KEY（或旧的 PARATERA_API_KEY）未设置，先设置好 API Key 再运行。")
        # timeout/max_retries=0：demo 场景下卡死比失败更糟，宁可快速报错重试，不要静默挂起
        _client = OpenAI(api_key=key.strip(), base_url=BASE_URL, timeout=120.0, max_retries=0)
    return _client


def call_message(messages: list[dict], model: str = MODEL_FULL, **kwargs):
    """返回完整的 message 对象（不只是文本），给需要读 tool_calls 的调用方用（比如 restaurant_agent.py 的工具调用循环）。"""
    response = _get_client().chat.completions.create(model=model, messages=messages, **kwargs)
    if not response.choices:
        raise RuntimeError("模型没有返回任何 choice")
    return response.choices[0].message


def call_llm(messages: list[dict], model: str = MODEL_FULL, **kwargs) -> str:
    message = call_message(messages, model=model, **kwargs)
    if not message.content or not message.content.strip():
        raise RuntimeError("模型没有返回文本内容")
    return message.content


if __name__ == "__main__":
    # 最小连通性测试：只验证 API Key / base_url / model 能不能跑通，不涉及任何 Agent 逻辑
    for model in (MODEL_LIGHT, MODEL_FULL):
        print(model, "回复:", call_llm([{"role": "user", "content": "用一句话介绍你自己"}], model=model))
