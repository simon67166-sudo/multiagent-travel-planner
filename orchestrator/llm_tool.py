"""Shared Paratera client. Process environment > root .env > legacy orchestrator/.env."""
import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / "orchestrator" / ".env")
BASE_URL = os.getenv("LLM_BASE_URL", "https://llmapi.paratera.com/v1").strip()
MODEL_FULL = os.getenv("LLM_MODEL_PRO", "DeepSeek-V4-Pro-0813").strip()
MODEL_LIGHT = os.getenv("LLM_MODEL_FLASH", "DeepSeek-V4-Flash-0731").strip()
_client = None

def _get_client():
    global _client
    if _client is None:
        key = os.getenv("LLM_API_KEY") or os.getenv("PARATERA_API_KEY")
        if not key or not key.strip():
            raise RuntimeError("Set LLM_API_KEY (or legacy PARATERA_API_KEY) in .env")
        _client = OpenAI(api_key=key.strip(), base_url=BASE_URL, timeout=120.0, max_retries=0)
    return _client

def call_message(messages: list[dict], model: str = MODEL_FULL, **kwargs):
    response = _get_client().chat.completions.create(model=model, messages=messages, **kwargs)
    if not response.choices:
        raise RuntimeError("Model returned no choices")
    return response.choices[0].message

def call_llm(messages: list[dict], model: str = MODEL_FULL, **kwargs) -> str:
    message = call_message(messages, model=model, **kwargs)
    if not message.content or not message.content.strip():
        raise RuntimeError("Model returned no text")
    return message.content

if __name__ == "__main__":
    for model in (MODEL_LIGHT, MODEL_FULL):
        print(model, call_llm([{"role": "user", "content": "Reply OK"}], model=model))
