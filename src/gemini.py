"""调用 Cloudsway 上的 Gemini 3.1 Flash Lite。"""

from __future__ import annotations

import time
from typing import Any

import requests

from .config import gemini_settings


def _split_data_uri(uri: str) -> tuple[str, str]:
    """把 data URI 拆成 (mime, base64)。非 data URI 原样返回 (mime, uri)。"""
    if uri.startswith("data:"):
        head, _, data = uri.partition(",")
        mime = head[len("data:") :].split(";")[0]
        return mime, data
    return "", uri


def _to_native_parts(user: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 OpenAI 风格 content（或纯文本）转成 Gemini 原生 parts。

    - 纯文本 → [{"text": ...}]
    - image_url/video_url/audio_url 的 data URI → {"inlineData": {"mimeType", "data"}}
    """
    if isinstance(user, str):
        return [{"text": user}]
    parts: list[dict[str, Any]] = []
    for item in user:
        kind = item.get("type")
        if kind == "text":
            parts.append({"text": item["text"]})
            continue
        url = ""
        if kind in ("image_url", "video_url", "audio_url"):
            url = (item.get(kind) or {}).get("url", "")
        if not url:
            continue
        mime, data = _split_data_uri(url)
        parts.append({"inlineData": {"mimeType": mime or "application/octet-stream", "data": data}})
    return parts


def _chat_native(
    cfg: dict[str, Any],
    system: str,
    user: str | list[dict[str, Any]],
    *,
    temperature: float,
    top_p: float,
    timeout: float,
    retries: int,
) -> str:
    """走 Gemini 原生 generateContent 端点（支持图/视频/音频/PDF）。"""
    url = cfg.get("native_url") or ""
    if not url:
        raise RuntimeError("原生端点 URL 为空：请配置 GEMINI_NATIVE_API_URL 或 endpoint")
    parts = _to_native_parts(user)
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "systemInstruction": {"parts": [{"text": system}]},
        "generationConfig": {"temperature": temperature, "topP": top_p},
    }
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise RuntimeError(f"Gemini 无候选输出: {data!r}")
            out_parts = ((candidates[0].get("content") or {}).get("parts")) or []
            text = "".join(p.get("text", "") for p in out_parts).strip()
            if not text:
                raise RuntimeError(f"Gemini 空回复: {data!r}")
            return text
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt + 1 < retries:
                time.sleep(1.5**attempt)
    raise RuntimeError(f"Gemini 请求失败（{retries} 次）: {last_err}") from last_err


def chat(
    system: str,
    user: str | list[dict[str, Any]],
    *,
    stage: str = "expand",
) -> str:
    """
    一次 chat/completions 或 Gemini 原生 generateContent。

    Args:
        system: SYSTEM 文本
        user: 纯字符串，或 OpenAI 风格多模态 content 列表
        stage: perceive / expand / format，决定温度

    根据 configs/gemini.yaml 的 protocol 自动选择端点：
    - native（默认）：generateContent，支持图/视频/音频/PDF
    - openai：chat/completions，仅文本/图片
    """
    cfg = gemini_settings()
    if not cfg["api_key"]:
        raise RuntimeError("缺少 Gemini API Key：设 GEMINI_API_KEY 或填写 configs/gemini.yaml")
    decode = (cfg.get("decode") or {}).get(stage) or {}
    temperature = float(decode.get("temperature", 0.4))
    top_p = float(decode.get("top_p", 0.95))
    retries = int(cfg["max_retries"])
    timeout = float(cfg["timeout_sec"])

    if cfg.get("protocol") == "native":
        return _chat_native(cfg, system, user, temperature=temperature, top_p=top_p, timeout=timeout, retries=retries)

    # ---- OpenAI 兼容层（仅文本/图片）----
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
                time.sleep(1.5**attempt)
    raise RuntimeError(f"Gemini 请求失败（{retries} 次）: {last_err}") from last_err
