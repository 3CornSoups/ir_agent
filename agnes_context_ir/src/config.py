"""Resource paths and environment-only runtime configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
SKILLS = PROMPTS / "skills"
EXAMPLES = PROMPTS / "examples"
H3_GUIDE = ROOT / "h3-prompt-writing" / "references"
_CORE_STEM = "_core"

ALL_MODES = ("t2va", "i2va", "fl2va", "l2va", "r2va")
KEYFRAME_MODES = ("i2va", "fl2va", "l2va")

# 按模式装配的语法规则卡（文件名 stem，不含 .txt）
_COMMON_SYNTAX = (
    "s01_output_contract",
    "s03_shots_timeline",
    "s04_camera",
    "s05_speakers",
    "s06_dialogue",
    "s07_onscreen_text",
    "s08_sound_fields",
    "s09_density",
    "s10_production_params",
)
_PLAN_COMMON = ("p01_cast", "p02_shotplan", "s03_shots_timeline", "s05_speakers", "s06_dialogue")

SKILL_SETS: dict[str, tuple[str, ...]] = {
    "format_t2va": _COMMON_SYNTAX,
    "format_keyframe": ("s02_alignment",) + _COMMON_SYNTAX,
    "format_r2va": _COMMON_SYNTAX
    + ("r01_ref_labels", "r02_summary", "r03_retention", "r04_detailed_desc"),
    "plan_t2va": _PLAN_COMMON + ("s04_camera", "s10_production_params"),
    "plan_keyframe": _PLAN_COMMON + ("s02_alignment", "s04_camera", "s10_production_params"),
    "plan_r2va_r1": _PLAN_COMMON
    + (
        "p03_retention_plan",
        "p04_tasktype",
        "r01_ref_labels",
        "s04_camera",
        "s10_production_params",
    ),
    "plan_r2va_r2": _PLAN_COMMON
    + (
        "p03_retention_plan",
        "p04_tasktype",
        "r01_ref_labels",
        "r02_summary",
        "r03_retention",
        "s04_camera",
        "s10_production_params",
    ),
}


def load_prompt(stem: str) -> str:
    """读取 prompts/{stem}.txt。"""
    path = PROMPTS / f"{stem}.txt"
    return path.read_text(encoding="utf-8").strip() + "\n"


def load_skill(stem: str) -> str:
    """读取 prompts/skills/{stem}.txt。"""
    path = SKILLS / f"{stem}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"缺少语法规则卡: {path}")
    return path.read_text(encoding="utf-8").strip() + "\n"


def load_skills(set_name: str) -> str:
    """按 SKILL_SETS 名称拼接一组规则卡。"""
    stems = SKILL_SETS.get(set_name)
    if not stems:
        raise ValueError(f"未知 skill set: {set_name}")
    chunks = [load_skill(stem).rstrip() for stem in stems]
    return "\n\n".join(chunks) + "\n"


def load_example(mode: str) -> str:
    """读取 prompts/examples/{mode}.txt（单个官方 Case）。"""
    mode = mode.lower().strip()
    if mode not in ALL_MODES:
        raise ValueError(f"未知模式: {mode}")
    path = EXAMPLES / f"{mode}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"缺少官方示例: {path}")
    return path.read_text(encoding="utf-8").strip() + "\n"


def format_skill_set_name(mode: str) -> str:
    """返回格式轮应装配的 skill set 名。"""
    mode = mode.lower().strip()
    if mode == "t2va":
        return "format_t2va"
    if mode in KEYFRAME_MODES:
        return "format_keyframe"
    if mode == "r2va":
        return "format_r2va"
    raise ValueError(f"未知模式: {mode}")


def plan_skill_set_name(mode: str, *, stage: str = "") -> str:
    """返回规划轮应装配的 skill set 名。"""
    mode = mode.lower().strip()
    if mode == "t2va":
        return "plan_t2va"
    if mode in KEYFRAME_MODES:
        return "plan_keyframe"
    if mode == "r2va":
        if stage in ("r2_plot", "plot"):
            return "plan_r2va_r2"
        return "plan_r2va_r1"
    raise ValueError(f"未知模式: {mode}")


def load_round_system(stem: str, *, with_core: bool = False) -> str:
    """读取某轮 SYSTEM：阶段正文 + 可选前置 _core 保真规则。"""
    parts: list[str] = [load_prompt(stem).strip()]
    if with_core:
        core_path = PROMPTS / f"{_CORE_STEM}.txt"
        if core_path.is_file():
            parts.insert(0, core_path.read_text(encoding="utf-8").strip())
    return "\n\n---\n\n".join(parts) + "\n"


def load_official_guide(mode: str) -> str:
    """读取 MiniMax 官方英文写作指南全文（溯源底本；格式轮默认不再整段注入）。"""
    mode = mode.lower().strip()
    if mode not in ALL_MODES:
        raise ValueError(f"未知模式: {mode}，可选 {', '.join(ALL_MODES)}")
    name = "ref-en.txt" if mode == "r2va" else "base-en.txt"
    path = H3_GUIDE / name
    if not path.is_file():
        raise FileNotFoundError(f"缺少官方写作指南: {path}")
    return path.read_text(encoding="utf-8").strip() + "\n"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw is None or not raw.strip() else float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or not raw.strip() else int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        raise ValueError(f"{name} must be a boolean")


def _decode_stage(stage: str, temperature: float, top_p: float) -> dict[str, float]:
    prefix = f"AGNES_CONTEXT_IR_{stage.upper()}"
    return {
        "temperature": _env_float(f"{prefix}_TEMPERATURE", temperature),
        "top_p": _env_float(f"{prefix}_TOP_P", top_p),
    }


def gemini_settings() -> dict[str, Any]:
    """Read embedded ContextIR Gemini settings exclusively from environment."""
    api_key = os.environ.get("AGNES_GEMINI_API_KEY", "").strip()
    endpoint = os.environ.get("AGNES_GEMINI_ENDPOINT", "").strip()
    model = os.environ.get("AGNES_GEMINI_MODEL", "").strip()
    text_model = os.environ.get("AGNES_GEMINI_TEXT_MODEL", "").strip() or model
    enable_thinking = _env_bool("AGNES_GEMINI_ENABLE_THINKING", False)
    api_url = os.environ.get("AGNES_GEMINI_API_URL", "").strip()
    if not api_url and endpoint:
        api_url = (
            f"https://genaiapi.cloudsway.net/v1/ai/{endpoint}"
            "/google/chat/completions"
        )

    protocol = os.environ.get(
        "AGNES_CONTEXT_IR_GEMINI_PROTOCOL", "native"
    ).strip().lower()
    native_url = os.environ.get(
        "AGNES_CONTEXT_IR_GEMINI_NATIVE_API_URL", ""
    ).strip()
    if protocol == "native" and not native_url:
        if "google/chat/completions" in api_url:
            native_url = api_url.replace(
                "/google/chat/completions", "/generateContent"
            )
        elif "/chat/completions" in api_url:
            native_url = api_url.replace("/chat/completions", "/generateContent")
        elif endpoint:
            native_url = (
                f"https://genaiapi.cloudsway.net/v1/ai/{endpoint}/generateContent"
            )

    decode = {
        "extract_dialogue": _decode_stage("extract_dialogue", 0.0, 1.0),
        "resolve_speakers": _decode_stage("resolve_speakers", 0.0, 1.0),
        "perceive": _decode_stage("perceive", 0.2, 0.9),
        "parse_intent": _decode_stage("parse_intent", 0.0, 1.0),
        "route": _decode_stage("route", 0.1, 0.9),
        "expand": _decode_stage("expand", 0.5, 0.95),
        "elaborate": _decode_stage("elaborate", 0.5, 0.9),
        "format": _decode_stage("format", 0.2, 0.9),
        "fidelity": _decode_stage("fidelity", 0.0, 1.0),
        "verify": _decode_stage("verify", 0.2, 0.9),
    }
    return {
        "api_key": api_key,
        "endpoint": endpoint,
        "model": model,
        "text_model": text_model,
        "enable_thinking": enable_thinking,
        "api_url": api_url,
        "native_url": native_url,
        "protocol": protocol,
        "timeout_sec": _env_float("AGNES_GEMINI_TIMEOUT_S", 300),
        "max_retries": _env_int("AGNES_GEMINI_MAX_RETRIES", 3),
        "max_tokens": _env_int("AGNES_CONTEXT_IR_MAX_TOKENS", 8192),
        "decode": decode,
        "verify": {
            "intent_llm": _env_bool(
                "AGNES_CONTEXT_IR_VERIFY_INTENT_LLM", False
            ),
            "max_fix_rounds": max(
                0, _env_int("AGNES_CONTEXT_IR_MAX_FIX_ROUNDS", 1)
            ),
        },
    }


def media_settings() -> dict[str, Any]:
    shared_timeout = _env_float("AGNES_MEDIA_DOWNLOAD_TIMEOUT_S", 60)
    return {
        "download_timeout_sec": _env_float(
            "AGNES_CONTEXT_IR_MEDIA_DOWNLOAD_TIMEOUT_S",
            min(shared_timeout, 20),
        ),
        "download_max_retries": max(
            0, _env_int("AGNES_CONTEXT_IR_MEDIA_DOWNLOAD_MAX_RETRIES", 2)
        ),
        "download_retry_backoff_sec": max(
            0.0,
            _env_float(
                "AGNES_CONTEXT_IR_MEDIA_DOWNLOAD_RETRY_BACKOFF_S", 1.0
            ),
        ),
        "max_download_bytes": _env_int(
            "AGNES_MAX_MEDIA_DOWNLOAD_BYTES", 64 * 1024 * 1024
        ),
        "max_image_bytes": _env_int(
            "AGNES_MAX_IMAGE_BYTES", 30 * 1024 * 1024
        ),
    }


def h3_settings() -> dict[str, Any]:
    """Environment-only settings retained for the vendored optional CLI path."""
    return {
        "api_key": os.environ.get("MINIMAX_API_KEY", ""),
        "base_url": os.environ.get(
            "MINIMAX_BASE_URL", "https://api.minimaxi.com"
        ).rstrip("/"),
        "model": os.environ.get("MINIMAX_MODEL", "MiniMax-H3"),
        "skip_auth": _env_bool("H3_SKIP_AUTH", False),
        "timeout_sec": _env_float("H3_TIMEOUT_SEC", 120),
        "generate_path": os.environ.get(
            "H3_GENERATE_PATH", "/v2/video_generation"
        ),
        "query_path_template": os.environ.get(
            "H3_QUERY_PATH_TEMPLATE", "/v2/query/video_generation/{task_id}"
        ),
        "poll_interval_sec": _env_float("H3_POLL_INTERVAL_SEC", 5),
        "poll_timeout_sec": _env_float("H3_POLL_TIMEOUT_SEC", 1800),
        "default_resolution": os.environ.get("H3_DEFAULT_RESOLUTION", "768P"),
        "default_duration": _env_int("H3_DEFAULT_DURATION", 5),
        "default_ratio": os.environ.get("H3_DEFAULT_RATIO", "16:9"),
    }


def judge_settings() -> dict[str, Any]:
    kwargs_raw = os.environ.get(
        "JUDGE_CHAT_TEMPLATE_KWARGS", '{"enable_thinking": false}'
    )
    kwargs = json.loads(kwargs_raw)
    if not isinstance(kwargs, dict):
        raise ValueError("JUDGE_CHAT_TEMPLATE_KWARGS must be a JSON object")
    return {
        "base_url": os.environ.get(
            "JUDGE_BASE_URL", "http://127.0.0.1:8091"
        ).rstrip("/"),
        "api_key": os.environ.get("JUDGE_API_KEY", "EMPTY"),
        "model": os.environ.get("JUDGE_MODEL", "qwen3.8"),
        "timeout_sec": _env_float("JUDGE_TIMEOUT_SEC", 300),
        "max_retries": _env_int("JUDGE_MAX_RETRIES", 2),
        "max_tokens": _env_int("JUDGE_MAX_TOKENS", 4096),
        "temperature": _env_float("JUDGE_TEMPERATURE", 0.1),
        "chat_template_kwargs": kwargs,
    }
