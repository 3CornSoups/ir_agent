"""路径与 YAML 配置。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
CONFIGS = ROOT / "configs"


def load_yaml(name: str) -> dict[str, Any]:
    """读取 configs/{name}.yaml。"""
    path = CONFIGS / f"{name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置不是字典: {path}")
    return data


def load_prompt(stem: str) -> str:
    """读取 prompts/{stem}.txt。"""
    path = PROMPTS / f"{stem}.txt"
    return path.read_text(encoding="utf-8").strip() + "\n"


def gemini_settings() -> dict[str, Any]:
    """合并 Gemini 配置与环境变量。"""
    cfg = load_yaml("gemini")
    api_key = os.environ.get("GEMINI_API_KEY") or str(cfg.get("api_key") or "")
    endpoint = os.environ.get("GEMINI_ENDPOINT") or str(cfg.get("endpoint") or "")
    model = os.environ.get("GEMINI_MODEL") or str(cfg.get("model") or "")
    api_url = os.environ.get("GEMINI_API_URL") or str(cfg.get("api_url") or "")
    if not api_url:
        api_url = f"https://genaiapi.cloudsway.net/v1/ai/{endpoint}/google/chat/completions"
    return {
        "api_key": api_key,
        "endpoint": endpoint,
        "model": model,
        "api_url": api_url,
        "timeout_sec": float(cfg.get("timeout_sec") or 300),
        "max_retries": int(cfg.get("max_retries") or 3),
        "decode": cfg.get("decode") or {},
    }


def h3_settings() -> dict[str, Any]:
    """合并 H3 出片配置与环境变量。"""
    cfg = load_yaml("h3")
    api_key = os.environ.get("MINIMAX_API_KEY") or str(cfg.get("api_key") or "")
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
