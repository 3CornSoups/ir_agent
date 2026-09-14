"""调用 Cloudsway 上的 Gemini 3.1 Flash Lite。"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import gemini_settings
from .runlog import log_model_call

logger = logging.getLogger(__name__)

# Align with app.prompt_enhance._TRANSIENT_STATUS
_TRANSIENT_HTTP_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=30, pool_maxsize=30, max_retries=Retry(total=0))
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


def _http_error_body_log_limit() -> int:
    raw = os.environ.get("AGNES_GEMINI_HTTP_ERROR_BODY_LOG_LIMIT", "8192")
    try:
        return max(256, int(raw))
    except ValueError:
        return 8192


def _response_error_text(resp: requests.Response | None) -> str:
    if resp is None:
        return ""
    try:
        text = resp.text
    except Exception:
        return ""
    text = text.strip()
    if not text:
        return ""
    limit = _http_error_body_log_limit()
    if len(text) > limit:
        return (
            f"{text[:limit]}…(truncated, total={len(text)} chars)"
        )
    return text


def _format_requests_http_error(
    exc: requests.HTTPError,
    *,
    stage: str,
    url: str,
    protocol: str,
) -> str:
    resp = exc.response
    status = resp.status_code if resp is not None else "?"
    body = _response_error_text(resp)
    parts = [
        f"HTTP {status}",
        f"stage={stage}",
        f"protocol={protocol}",
        f"url={url}",
    ]
    if body:
        parts.append(f"response_body={body}")
    elif resp is not None:
        parts.append("response_body=<empty>")
    return " ".join(parts)


def _log_gemini_http_error(
    detail: str,
    *,
    stage: str,
    attempt: int,
    retries: int,
    will_retry: bool,
    elapsed_ms: int | None = None,
) -> None:
    timing = f" elapsed_ms={elapsed_ms}" if elapsed_ms is not None else ""
    if will_retry:
        logger.warning(
            "gemini http error stage=%s attempt=%s/%s will_retry=true%s %s",
            stage,
            attempt,
            retries,
            timing,
            detail,
        )
        return
    logger.error(
        "gemini http error stage=%s attempt=%s/%s will_retry=false%s %s",
        stage,
        attempt,
        retries,
        timing,
        detail,
    )


def _input_chars(system: str, user: str | list[dict[str, Any]]) -> int:
    """Count text prompt chars (system + user text); ignore media payloads."""
    n = len(system or "")
    if isinstance(user, str):
        return n + len(user)
    for item in user or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            n += len(str(item.get("text") or ""))
        elif "text" in item and item.get("type") is None:
            n += len(str(item.get("text") or ""))
    return n


def _log_gemini_call(
    *,
    stage: str,
    protocol: str,
    elapsed_ms: int,
    ok: bool,
    attempt: int,
    retries: int,
    input_chars: int | None = None,
    out_chars: int | None = None,
    err: str | None = None,
    model: str | None = None,
) -> None:
    """Worker-visible e2e timing for one Gemini chat() invocation attempt."""
    in_n = input_chars if input_chars is not None else 0
    model_s = (model or "").strip() or "-"
    if ok:
        logger.info(
            "gemini call stage=%s protocol=%s model=%s attempt=%s/%s elapsed_ms=%s "
            "ok=true input_chars=%s out_chars=%s",
            stage,
            protocol,
            model_s,
            attempt,
            retries,
            elapsed_ms,
            in_n,
            out_chars if out_chars is not None else 0,
        )
        return
    logger.warning(
        "gemini call stage=%s protocol=%s model=%s attempt=%s/%s elapsed_ms=%s "
        "ok=false input_chars=%s err=%s",
        stage,
        protocol,
        model_s,
        attempt,
        retries,
        elapsed_ms,
        in_n,
        (err or "")[:300],
    )


def _http_error_retryable(exc: requests.HTTPError) -> bool:
    resp = exc.response
    if resp is None:
        return True
    return resp.status_code in _TRANSIENT_HTTP_STATUS


def _raise_gemini_failure(err: Exception, *, attempts: int) -> None:
    raise RuntimeError(f"Gemini 请求失败（{attempts} 次）: {err}") from err


def _process_http_error(
    exc: requests.HTTPError,
    *,
    stage: str,
    url: str,
    protocol: str,
    attempt: int,
    retries: int,
    elapsed_ms: int | None = None,
) -> tuple[RuntimeError, bool]:
    detail = _format_requests_http_error(
        exc, stage=stage, url=url, protocol=protocol
    )
    retryable = _http_error_retryable(exc)
    will_retry = retryable and attempt + 1 < retries
    _log_gemini_http_error(
        detail,
        stage=stage,
        attempt=attempt + 1,
        retries=retries,
        will_retry=will_retry,
        elapsed_ms=elapsed_ms,
    )
    return RuntimeError(detail), will_retry


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
    - image_url/video_url/audio_url 的 data URI → inlineData
    - input_audio → inlineData（OpenAI 兼容音频）
    - http(s) URL → 下载后 inlineData（兜底）
    """
    from .config import media_settings
    from .media import (
        VIDEO_INLINE_LIMIT_BYTES,
        as_data_uri,
        cleanup_materialized_path,
        is_http_url,
        materialize_media_path,
    )

    if isinstance(user, str):
        return [{"text": user}]
    parts: list[dict[str, Any]] = []
    mcfg = media_settings()
    kind_to_media = {
        "image_url": "image",
        "video_url": "video",
        "audio_url": "audio",
    }
    for item in user:
        kind = item.get("type")
        if kind == "text":
            parts.append({"text": item["text"]})
            continue
        if kind == "input_audio":
            inp = item.get("input_audio") or {}
            data = str(inp.get("data") or "").strip()
            if not data:
                continue
            fmt = str(inp.get("format") or "mp3")
            mime = "audio/mpeg" if fmt == "mp3" else f"audio/{fmt}"
            parts.append({"inlineData": {"mimeType": mime, "data": data}})
            continue
        url = ""
        if kind in ("image_url", "video_url", "audio_url"):
            url = (item.get(kind) or {}).get("url", "")
        if not url:
            continue
        if is_http_url(url):
            media_kind = kind_to_media.get(kind, "image")
            original_url = url
            path = materialize_media_path(original_url, media_kind, mcfg)
            try:
                max_video = (
                    VIDEO_INLINE_LIMIT_BYTES if media_kind == "video" else None
                )
                url = as_data_uri(path, media_kind, max_video_bytes=max_video)
            finally:
                cleanup_materialized_path(path, original_url)
        mime, data = _split_data_uri(url)
        if not data:
            continue
        parts.append(
            {
                "inlineData": {
                    "mimeType": mime or "application/octet-stream",
                    "data": data,
                }
            }
        )
    return parts


def _chat_native(
    cfg: dict[str, Any],
    system: str,
    user: str | list[dict[str, Any]],
    *,
    stage: str,
    temperature: float,
    top_p: float,
    timeout: float,
    retries: int,
) -> str:
    """走 Gemini 原生 generateContent 端点（支持图/视频/音频/PDF）。"""
    t_call = time.monotonic()
    in_chars = _input_chars(system, user)
    url = cfg.get("native_url") or ""
    if not url:
        raise RuntimeError("原生端点 URL 为空：请配置 GEMINI_NATIVE_API_URL 或 endpoint")
    parts = _to_native_parts(user)
    generation_config: dict[str, Any] = {
        "temperature": temperature,
        "topP": top_p,
        "maxOutputTokens": int(cfg["max_tokens"]),
    }
    # 默认关闭思考模式以降低延迟；可通过 AGNES_GEMINI_ENABLE_THINKING=true 打开。
    if not cfg.get("enable_thinking"):
        generation_config["thinkingConfig"] = {
            "thinkingBudget": 0,
            "includeThoughts": False,
        }
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "systemInstruction": {"parts": [{"text": system}]},
        "generationConfig": generation_config,
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
        ],
    }
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    if retries < 1:
        raise ValueError("Gemini max_retries must be >= 1")

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = _session.post(url, json=body, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                feedback = data.get("promptFeedback") or {}
                block = feedback.get("blockReason") or feedback.get("block_reason")
                raise RuntimeError(
                    f"Gemini 无候选输出: blockReason={block!r} body={data!r}"
                )
            cand0 = candidates[0] or {}
            finish = cand0.get("finishReason") or cand0.get("finish_reason")
            out_parts = ((cand0.get("content") or {}).get("parts")) or []
            text = "".join(p.get("text", "") for p in out_parts).strip()
            if not text:
                raise RuntimeError(
                    f"Gemini 空回复: finishReason={finish!r} body={data!r}"
                )
            _log_gemini_call(
                stage=stage,
                protocol="native",
                model=str(cfg.get("model") or ""),
                elapsed_ms=int((time.monotonic() - t_call) * 1000),
                ok=True,
                attempt=attempt + 1,
                retries=retries,
                input_chars=in_chars,
                out_chars=len(text),
            )
            return text
        except requests.HTTPError as exc:
            elapsed_ms = int((time.monotonic() - t_call) * 1000)
            last_err, will_retry = _process_http_error(
                exc,
                stage=stage,
                url=url,
                protocol="native",
                attempt=attempt,
                retries=retries,
                elapsed_ms=elapsed_ms,
            )
            _log_gemini_call(
                stage=stage,
                protocol="native",
                model=str(cfg.get("model") or ""),
                elapsed_ms=elapsed_ms,
                ok=False,
                attempt=attempt + 1,
                retries=retries,
                input_chars=in_chars,
                err=str(last_err),
            )
            if not will_retry:
                _raise_gemini_failure(last_err, attempts=attempt + 1)
            time.sleep(1.5**attempt)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            elapsed_ms = int((time.monotonic() - t_call) * 1000)
            if attempt + 1 < retries:
                logger.warning(
                    "gemini error stage=%s attempt=%s/%s will_retry=true elapsed_ms=%s err=%s",
                    stage,
                    attempt + 1,
                    retries,
                    elapsed_ms,
                    exc,
                )
                _log_gemini_call(
                    stage=stage,
                    protocol="native",
                    model=str(cfg.get("model") or ""),
                    elapsed_ms=elapsed_ms,
                    ok=False,
                    attempt=attempt + 1,
                    retries=retries,
                    input_chars=in_chars,
                    err=str(exc),
                )
                time.sleep(1.5**attempt)
            else:
                _log_gemini_call(
                    stage=stage,
                    protocol="native",
                    model=str(cfg.get("model") or ""),
                    elapsed_ms=elapsed_ms,
                    ok=False,
                    attempt=attempt + 1,
                    retries=retries,
                    input_chars=in_chars,
                    err=str(exc),
                )
    raise RuntimeError(f"Gemini 请求失败（{retries} 次）: {last_err}") from last_err


def chat(
    system: str,
    user: str | list[dict[str, Any]],
    *,
    stage: str = "expand",
    model: str | None = None,
) -> str:
    """
    一次 chat/completions 或 Gemini 原生 generateContent。

    Args:
        system: SYSTEM 文本
        user: 纯字符串，或 OpenAI 风格多模态 content 列表
        stage: perceive / expand / format，决定温度
        model: 可选覆盖 ``AGNES_GEMINI_MODEL``（同 key/URL；用于 lite PE）

    根据全局环境变量中的 protocol 配置选择端点：
    - native（默认）：generateContent，支持图/视频/音频/PDF
    - openai：chat/completions；图片与视频统一经 image_url（http 直传或本地 data URI）
    """
    cfg = dict(gemini_settings())
    if not cfg["api_key"]:
        raise RuntimeError("缺少 Gemini API Key：请设置 AGNES_GEMINI_API_KEY")
    override = (model or "").strip()
    if override:
        cfg["model"] = override
    decode = (cfg.get("decode") or {}).get(stage) or {}
    temperature = float(decode.get("temperature", 0.4))
    top_p = float(decode.get("top_p", 0.95))
    retries = int(cfg["max_retries"])
    timeout = float(cfg["timeout_sec"])

    if cfg.get("protocol") == "native":
        try:
            text = _chat_native(
                cfg,
                system,
                user,
                stage=stage,
                temperature=temperature,
                top_p=top_p,
                timeout=timeout,
                retries=retries,
            )
        except Exception as exc:  # noqa: BLE001
            log_model_call(stage=stage, system=system, user=user, response=str(exc), ok=False)
            raise
        log_model_call(stage=stage, system=system, user=user, response=text, ok=True)
        return text

    # ---- OpenAI 兼容层（文本 / 图片 / 视频 URL；音频为 input_audio）----
    content: str | list[dict[str, Any]] = user
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]
    body = {
        "model": cfg["model"],
        "max_tokens": int(cfg["max_tokens"]),
        "temperature": temperature,
        "top_p": top_p,
        "stream": False,
        "messages": messages,
    }
    # OpenAI 兼容网关：关闭思考（字段兼容常见代理；未知字段多数会忽略）。
    if not cfg.get("enable_thinking"):
        body["enable_thinking"] = False
        body["chat_template_kwargs"] = {"enable_thinking": False}
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    if retries < 1:
        raise ValueError("Gemini max_retries must be >= 1")

    api_url = str(cfg["api_url"])
    t_call = time.monotonic()
    in_chars = _input_chars(system, user)
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = _session.post(api_url, json=body, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise RuntimeError(f"Gemini 空回复: {data!r}")
            text = text.strip()
            _log_gemini_call(
                stage=stage,
                protocol="openai",
                model=str(cfg.get("model") or ""),
                elapsed_ms=int((time.monotonic() - t_call) * 1000),
                ok=True,
                attempt=attempt + 1,
                retries=retries,
                input_chars=in_chars,
                out_chars=len(text),
            )
            log_model_call(stage=stage, system=system, user=user, response=text, ok=True)
            return text
        except requests.HTTPError as exc:
            elapsed_ms = int((time.monotonic() - t_call) * 1000)
            last_err, will_retry = _process_http_error(
                exc,
                stage=stage,
                url=api_url,
                protocol="openai",
                attempt=attempt,
                retries=retries,
                elapsed_ms=elapsed_ms,
            )
            _log_gemini_call(
                stage=stage,
                protocol="openai",
                model=str(cfg.get("model") or ""),
                elapsed_ms=elapsed_ms,
                ok=False,
                attempt=attempt + 1,
                retries=retries,
                input_chars=in_chars,
                err=str(last_err),
            )
            if not will_retry:
                log_model_call(
                    stage=stage,
                    system=system,
                    user=user,
                    response=str(last_err),
                    ok=False,
                )
                _raise_gemini_failure(last_err, attempts=attempt + 1)
            time.sleep(1.5**attempt)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            elapsed_ms = int((time.monotonic() - t_call) * 1000)
            if attempt + 1 < retries:
                logger.warning(
                    "gemini error stage=%s attempt=%s/%s will_retry=true elapsed_ms=%s err=%s",
                    stage,
                    attempt + 1,
                    retries,
                    elapsed_ms,
                    exc,
                )
                _log_gemini_call(
                    stage=stage,
                    protocol="openai",
                    model=str(cfg.get("model") or ""),
                    elapsed_ms=elapsed_ms,
                    ok=False,
                    attempt=attempt + 1,
                    retries=retries,
                    input_chars=in_chars,
                    err=str(exc),
                )
                time.sleep(1.5**attempt)
            else:
                _log_gemini_call(
                    stage=stage,
                    protocol="openai",
                    model=str(cfg.get("model") or ""),
                    elapsed_ms=elapsed_ms,
                    ok=False,
                    attempt=attempt + 1,
                    retries=retries,
                    input_chars=in_chars,
                    err=str(exc),
                )
    log_model_call(stage=stage, system=system, user=user, response=str(last_err), ok=False)
    raise RuntimeError(f"Gemini 请求失败（{retries} 次）: {last_err}") from last_err
