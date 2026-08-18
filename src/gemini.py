"""调用 Cloudsway 上的 Gemini 3.1 Flash Lite。"""

from __future__ import annotations

import time
from typing import Any

import requests

from .config import gemini_settings


def chat(
    system: str,
    user: str | list[dict[str, Any]],
    *,
    stage: str = "expand",
) -> str:
    """
    一次 chat/completions。

    Args:
        system: SYSTEM 文本
        user: 纯字符串，或 OpenAI 风格多模态 content 列表
        stage: perceive / expand / format，决定温度

    Returns:
        assistant 文本
    """
    cfg = gemini_settings()
    if not cfg["api_key"]:
        raise RuntimeError("缺少 Gemini API Key：设 GEMINI_API_KEY 或填写 configs/gemini.yaml")
    decode = (cfg.get("decode") or {}).get(stage) or {}
    temperature = float(decode.get("temperature", 0.4))
    top_p = float(decode.get("top_p", 0.95))
    content: str | list[dict[str, Any]] = user
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]
    body = {
        "model": cfg["model"],
        "temperature": temperature,
        "top_p": top_p,
        "stream": False,
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    last_err: Exception | None = None
    retries = int(cfg["max_retries"])
    timeout = float(cfg["timeout_sec"])
    for attempt in range(retries):
        try:
            resp = requests.post(cfg["api_url"], json=body, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise RuntimeError(f"Gemini 空回复: {data!r}")
            return text.strip()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt + 1 < retries:
                time.sleep(1.5 ** attempt)
    raise RuntimeError(f"Gemini 请求失败（{retries} 次）: {last_err}") from last_err
