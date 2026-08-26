"""由 Intent Contract 推导 elaborate/format 复杂度预算（目标词数区间）。"""

from __future__ import annotations

import re
from typing import Any


def estimate_shot_count(contract: Any) -> int:
    """估计镜头数：单镜头=1，否则取 max_shots 或动作链长度。"""
    shot = getattr(contract, "shot_constraint", None)
    if shot is not None and getattr(shot, "single_shot", False):
        return 1
    max_shots = getattr(shot, "max_shots", None) if shot is not None else None
    if max_shots is not None:
        try:
            return max(1, int(max_shots))
        except (TypeError, ValueError):
            pass
    actions = list(getattr(contract, "action_chain", None) or [])
    return max(1, min(6, len(actions) or 1))


def has_filmic_locks(contract: Any) -> bool:
    """合同 must/风格是否含电影感成像锁（浅景深/电影感/live-action 等）。"""
    joined = " ".join(str(x) for x in (getattr(contract, "must_elements", None) or []))
    style = str(getattr(contract, "explicit_style", None) or "")
    raw = str(getattr(contract, "intent_raw", None) or "")
    blob = f"{joined} {style} {raw}"
    return bool(
        re.search(
            r"浅景深|深景深|电影感|live[- ]?action|cinematic|filmic|bokeh|"
            r"shallow\s+depth",
            blob,
            re.I,
        )
    )


def complexity_word_budget(contract: Any) -> tuple[int, int]:
    """按 镜头数 × 主体数 × 动作链长度 × 时长 给出目标英文词数 [lo, hi]。

    简单单镜头短片压下限，避免灌水；多拍多主体抬上限以拉高 EN1/EN5。
    含电影感视觉锁时抬高单镜下限，对齐官方 cinematic 密度（约 280–350 词）。
    """
    shots = estimate_shot_count(contract)
    subjects = max(1, len(getattr(contract, "must_elements", None) or []))
    actions = max(1, len(getattr(contract, "action_chain", None) or []))
    dur = float(getattr(contract, "duration_sec", None) or 5.0)
    dur = max(4.0, min(15.0, dur))
    # 基准：每「镜头×主体×动作」单元约 35 词，再按时长微调
    units = shots * subjects * actions
    mid = int(35 * units * (0.55 + dur / 12.0))
    mid = max(90, min(520, mid))
    lo = max(80, int(mid * 0.85))
    hi = min(650, int(mid * 1.35))
    if has_filmic_locks(contract):
        # 官方 neon 类约 280–330 词；抬下限并略抬上限，避免 elaborate 贴着低档写
        lo = max(lo, 280)
        hi = max(hi, min(650, lo + 120))
        mid = max(mid, (lo + hi) // 2)
    if hi < lo + 30:
        hi = lo + 30
    return lo, hi


def format_complexity_budget_block(contract: Any) -> str:
    """生成写入 elaborate USER 的复杂度预算块。"""
    lo, hi = complexity_word_budget(contract)
    shots = estimate_shot_count(contract)
    subjects = max(1, len(getattr(contract, "must_elements", None) or []))
    actions = max(1, len(getattr(contract, "action_chain", None) or []))
    dur = float(getattr(contract, "duration_sec", None) or 5.0)
    filmic = has_filmic_locks(contract)
    filmic_line = (
        "- FILMIC SINGLE-SHOT: target the UPPER half of the word budget with layered "
        "reflections, shallow/bokeh depth planes, micro-motion (mist, rain streaks, "
        "tire spray), diegetic ambience — without inventing new entities.\n"
        if filmic
        else ""
    )
    return (
        "COMPLEXITY BUDGET (from Intent Contract; target the final scene prose):\n"
        f"- estimated_shots={shots}, must_elements={subjects}, action_steps={actions}, "
        f"duration_sec≈{dur:.0f}\n"
        f"- target_word_count: {lo}-{hi} English words for the enriched scene note "
        "(integrated description body after formatting).\n"
        f"{filmic_line}"
        "- Fill the budget with Anchored + Inferred detail: materials, light direction/quality, "
        "textures, camera type+amplitude+speed, diegetic foley/ambience, spatial layers.\n"
        "- Do NOT pad with Invented significant entities (new people, logos, captions, brands, "
        "locations, dialogue)."
    )


def densify_user_block(
    elaborated: str,
    *,
    contract_block: str,
    complexity_block: str,
    lo: int,
    hi: int,
    inventory: str | None = None,
) -> str:
    """构造 filmic 欠词 densify USER。"""
    lines = [
        f"The scene note is UNDER the COMPLEXITY BUDGET floor ({lo} words). "
        f"Rewrite it richer toward {lo}-{hi} English words.",
        "Keep every must_elements item and action_chain beat; especially keep near-foreground "
        "blur / 虚影掠过 as a DISTINCT near-lens action separate from the main subject.",
        "Add only Anchored+Inferred cinematic detail (reflections, DOF planes, micro-motion, "
        "diegetic ambience). Do NOT invent significant entities.",
        "",
        contract_block.strip(),
        "",
        complexity_block.strip(),
    ]
    if inventory:
        lines.extend(["", "Reference inventory:", inventory.strip()])
    lines.extend(["", "Scene note to densify:", elaborated.strip()])
    return "\n".join(lines)
