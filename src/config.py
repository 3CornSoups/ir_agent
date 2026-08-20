"""路径与 YAML 配置。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
CONFIGS = ROOT / "configs"
# 与 IR_Agent / 8.18 同一套 Cloudsway 凭证源。
YZ_GENERATE_PROMPT = ROOT.parent / "8.17" / "scripts" / "generate_prompt.py"


def load_yaml(name: str) -> dict[str, Any]:
    """读取 configs/{name}.yaml；没有则回退 {name}.yaml.example，再没有则空字典。

    密钥仍以环境变量优先。这样 --no-video 不必先复制 h3.yaml，
    出片/扩写也可以只靠 GEMINI_API_KEY / MINIMAX_API_KEY。
    """
    path = CONFIGS / f"{name}.yaml"
    fallback = CONFIGS / f"{name}.yaml.example"
    chosen = path if path.is_file() else fallback if fallback.is_file() else None
    if chosen is None:
        return {}
    data = yaml.safe_load(chosen.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置不是字典: {chosen}")
    return data


def load_prompt(stem: str) -> str:
    """读取 prompts/{stem}.txt。"""
    path = PROMPTS / f"{stem}.txt"
    return path.read_text(encoding="utf-8").strip() + "\n"


def _non_placeholder(value: str) -> str:
    """丢掉 example 里的 YOUR_* 占位，避免当成真密钥发出去。"""
    text = (value or "").strip()
    if not text:
        return ""
    upper = text.upper()
    if upper.startswith("YOUR_") or upper in {"CHANGEME", "TODO", "PLACEHOLDER"}:
        return ""
    return text


def _load_yz_cloudsway() -> tuple[str, str]:
    """从 8.17/scripts/generate_prompt.py 读 API_KEY / ENDPOINT；没有则空。"""
    path = YZ_GENERATE_PROMPT
    if not path.is_file():
        return "", ""
    text = path.read_text(encoding="utf-8")

    def grab(name: str) -> str:
        m = re.search(rf'^{name}\s*=\s*"([^"]*)"', text, flags=re.MULTILINE)
        return m.group(1).strip() if m else ""

    return grab("API_KEY"), grab("ENDPOINT")


def gemini_settings() -> dict[str, Any]:
    """合并 Gemini 配置：环境变量 > configs/gemini.yaml > 8.17 generate_prompt.py。"""
    cfg = load_yaml("gemini")
    yz_key, yz_endpoint = _load_yz_cloudsway()
    api_key = _non_placeholder(
        os.environ.get("GEMINI_API_KEY") or str(cfg.get("api_key") or "") or yz_key
    )
    endpoint = (
        os.environ.get("GEMINI_ENDPOINT") or str(cfg.get("endpoint") or "") or yz_endpoint
    ).strip()
    model = os.environ.get("GEMINI_MODEL") or str(cfg.get("model") or "")
    api_url = os.environ.get("GEMINI_API_URL") or str(cfg.get("api_url") or "")
    if not api_url:
        api_url = f"https://genaiapi.cloudsway.net/v1/ai/{endpoint}/google/chat/completions"
    # 协议：native=Gemini 原生 generateContent（支持视频/音频/PDF），openai=OpenAI 兼容层（仅文本/图片）
    protocol = (os.environ.get("GEMINI_PROTOCOL") or str(cfg.get("protocol") or "native")).strip().lower()
    # 原生端点 URL：默认由 endpoint 推导，或在 api_url 基础上替换路径段
    if protocol == "native":
        native_url = os.environ.get("GEMINI_NATIVE_API_URL") or str(cfg.get("native_api_url") or "")
        if not native_url:
            if "google/chat/completions" in api_url:
                # .../google/chat/completions -> .../generateContent（原生端点无 google/ 段）
                native_url = api_url.replace("/google/chat/completions", "/generateContent")
            elif "/chat/completions" in api_url:
                native_url = api_url.replace("/chat/completions", "/generateContent")
            else:
                native_url = f"https://genaiapi.cloudsway.net/v1/ai/{endpoint}/generateContent"
    else:
        native_url = ""
    # 质量校验行为配置（规则层无成本；LLM 层需显式开启）
    verify_cfg = cfg.get("verify") or {}
    return {
        "api_key": api_key,
        "endpoint": endpoint,
        "model": model,
        "api_url": api_url,
        "native_url": native_url,
        "protocol": protocol,
        "timeout_sec": float(cfg.get("timeout_sec") or 300),
        "max_retries": int(cfg.get("max_retries") or 3),
        "decode": cfg.get("decode") or {},
        "verify": {
            "intent_llm": bool(verify_cfg.get("intent_llm", False)),
            "max_fix_rounds": max(0, int(verify_cfg.get("max_fix_rounds", 1))),
        },
    }


def h3_settings() -> dict[str, Any]:
    """合并 H3 出片配置与环境变量。"""
    cfg = load_yaml("h3")
    api_key = _non_placeholder(os.environ.get("MINIMAX_API_KEY") or str(cfg.get("api_key") or ""))
    skip_auth_env = os.environ.get("H3_SKIP_AUTH", "").strip().lower()
    skip_auth = bool(cfg.get("skip_auth")) or skip_auth_env in {"1", "true", "yes", "on"}
    return {
        "api_key": api_key,
        "base_url": str(cfg.get("base_url") or "https://api.minimaxi.com").rstrip("/"),
        "model": str(cfg.get("model") or "MiniMax-H3"),
        "skip_auth": skip_auth,
        "timeout_sec": float(cfg.get("timeout_sec") or 120),
        "generate_path": str(cfg.get("generate_path") or "/v2/video_generation"),
        "query_path_template": str(cfg.get("query_path_template") or "/v2/query/video_generation/{task_id}"),
        "poll_interval_sec": float(cfg.get("poll_interval_sec") or 5),
        "poll_timeout_sec": float(cfg.get("poll_timeout_sec") or 1800),
        "default_resolution": str(cfg.get("default_resolution") or "768P"),
        "default_duration": int(cfg.get("default_duration") or 5),
        "default_ratio": str(cfg.get("default_ratio") or "16:9"),
    }
