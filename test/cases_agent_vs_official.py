# -*- coding: utf-8 -*-
"""Agent vs 官方 Context-IR 管线对比测试用例清单。

用例来源两类：
1. 从 zwb 目录现成资产中找的对照用例（source="zwb-asset"，含官方 IR 输出 official_*）
2. 需调用本地文生图/文生视频模型生成素材的用例（source="local-model"，由 test_local_model_cases 驱动）

官方 IR 对照资产位置：
- 母仓基准：/kwkj-k8s/zwb/应用/Qwen提示词/母仓/benchmarks/
- R2VA 对照项目：/kwkj-k8s/zwb/项目/R2VA_Qwen与官方对照/

字段命名：agent（enhance）与官方 IR 的 t2va 均为三字段，r2va 均为六段，
字段名一致，可直接按字段名做结构对比。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

ZW = Path("/kwkj-k8s/zwb")
MUCA = ZW / "应用" / "Qwen提示词" / "母仓" / "benchmarks"
MUCA_ROOT = ZW / "应用" / "Qwen提示词" / "母仓"
R2VA_CMP = ZW / "项目" / "R2VA_Qwen与官方对照"
FL2VA_CASES = ZW / "项目" / "FL2VA用例"

GOLD_IR = MUCA_ROOT / "out" / "gold_ir"
GOLD_IR_R2VA = MUCA_ROOT / "out" / "gold_ir_r2va"
QWEN_V1 = MUCA_ROOT / "out" / "qwen_intent_v1.1"
QWEN_R2VA = MUCA_ROOT / "out" / "qwen_intent_r2va_v1"
GOLD_IR_VIDEO = MUCA_ROOT / "out" / "videos" / "gold_ir"
QWEN_VIDEO = MUCA_ROOT / "out" / "videos" / "qwen_intent_v1.1"

T2VA_FIELDS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")
R2VA_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
FRAME_ALIGN_FIELDS = T2VA_FIELDS

ALIGN_HINTS = {
    "i2va": "fully referenced",
    "fl2va": "aligns with the 0.00-second",
    "l2va": "aligns with the",
}

FORBIDDEN_TOKENS = ("16:9", "9:16", "21:9", "4:3", "768P", "2K", "1080P", "fps", "1280x720", "1920x1080")

MEDIA_CASES_DIR = Path("/kwkj-k8s/zwb/项目/agent/new_agent0818/runs/generated_media/cases")


def _p(*parts: str) -> str:
    """把 zwb 相对路径拼成绝对路径字符串。"""
    return str(Path(*parts))


def read_intent(path: str) -> str:
    """读取意图文本（UTF-8）。"""
    return Path(path).read_text(encoding="utf-8").strip()


def _gold_ir(mode_id: str) -> str:
    """母仓 gold_ir 官方增强稿路径。"""
    return str(GOLD_IR / f"{mode_id}.txt")


def _gold_ir_r2va(case_id: str) -> str:
    """母仓 gold_ir_r2va 官方增强稿路径。"""
    return str(GOLD_IR_R2VA / f"{case_id}.txt")


def _qwen_v1(mode_id: str) -> str:
    """母仓 qwen_intent_v1.1 本地 Qwen 增强稿路径。"""
    return str(QWEN_V1 / f"{mode_id}.txt")


def _qwen_r2va(case_id: str) -> str:
    """母仓 qwen_intent_r2va_v1 本地 Qwen 增强稿路径。"""
    return str(QWEN_R2VA / f"{case_id}.txt")


def _gold_video(mode_id: str) -> str:
    """母仓 gold_ir 官方成片路径。"""
    return str(GOLD_IR_VIDEO / f"{mode_id}.mp4")


def _qwen_video(mode_id: str) -> str:
    """母仓 qwen_intent_v1.1 本地成片路径。"""
    return str(QWEN_VIDEO / f"{mode_id}.mp4")
