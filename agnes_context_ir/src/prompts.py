"""各轮 USER 消息组装与格式化 SYSTEM 拼接。"""

from __future__ import annotations

from pathlib import Path

from .config import (
    format_skill_set_name,
    load_example,
    load_official_guide,
    load_round_system,
    load_skills,
    plan_skill_set_name,
)


def production_block(
    *,
    duration: float | int,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    quality: str | None = None,
) -> str:
    """渲染制作参数说明块（仅供 LLM 参考，不写进 H3 字段）。"""
    lines = [
        "Production parameters (API only; do not write into MiniMax prompt fields):",
        f"- Duration: {float(duration):g} seconds (keep cut timestamps inside this length)",
    ]
    if aspect_ratio:
        lines.append(f"- Aspect ratio: {aspect_ratio}")
    if resolution:
        lines.append(f"- Resolution: {resolution}")
    if quality:
        lines.append(f"- Quality: {quality}")
    return "\n".join(lines)


def r1_keyinfo_user(
    intent: str,
    *,
    mode: str,
    duration: float | int,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    quality: str | None = None,
) -> str:
    """构造 t2va Round-1 USER 消息。"""
    return "\n".join(
        [
            f"Mode: {mode}",
            production_block(
                duration=duration,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                quality=quality,
            ),
            "",
            "Short intent:",
            intent.strip(),
        ]
    )


def r1_image_user(
    intent: str,
    *,
    mode: str,
    duration: float | int,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    quality: str | None = None,
) -> str:
    """构造 i2va/fl2va/l2va Round-1 USER 消息。"""
    mode_hints = {
        "i2va": (
            "Mode: I2VA. Attached image is <Picture 1>, the FIRST frame at 0.00s / [Shot 1]. "
            "Describe visible facts and how the scene develops forward from this frame."
        ),
        "fl2va": (
            "Mode: FL2VA. Two images in order: <Picture 1> = first frame at 0.00s; "
            f"<Picture 2> = last frame at {float(duration):.2f}s. "
            "Describe each and the motion path between them."
        ),
        "l2va": (
            "Mode: L2VA. Attached image is <Picture 1>, the LAST frame "
            f"(lands at about {float(duration):.2f}s). Describe the landing state and what precedes it."
        ),
    }
    return "\n".join(
        [
            mode_hints.get(mode, f"Mode: {mode}"),
            production_block(
                duration=duration,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                quality=quality,
            ),
            "",
            "Short intent:",
            intent.strip(),
        ]
    )


def _asset_label_display(path_or_ref: str, kind: str, index: int) -> str:
    """把素材引用收成短标签，避免 data URI / 绝对路径泄漏进 USER。"""
    raw = (path_or_ref or "").strip()
    tag = {"image": "Picture", "video": "Video", "audio": "Audio"}[kind]
    if raw.lower().startswith("data:"):
        mime = raw[5:].split(";", 1)[0] or kind
        return f"<{tag} {index}> = (inline {mime} data)"
    if raw.startswith(("http://", "https://")):
        name = Path(raw.split("?", 1)[0]).name or f"{kind}_{index}"
        return f"<{tag} {index}> = {name} (url)"
    name = Path(raw).name or f"{kind}_{index}"
    return f"<{tag} {index}> = {name}"


def r1_r2va_user(
    intent: str,
    *,
    duration: float | int,
    labels: list[str],
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    quality: str | None = None,
) -> str:
    """构造 r2va Round-1 USER 消息。"""
    asset_lines = "\n".join(labels) if labels else "(no assets listed)"
    return "\n".join(
        [
            "Mode: r2va",
            production_block(
                duration=duration,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                quality=quality,
            ),
            "",
            "Attached assets in order:",
            asset_lines,
            "",
            "Short intent:",
            intent.strip(),
        ]
    )


def r2_plot_user(intent: str, round1_output: str, *, duration: float | int) -> str:
    """构造 r2va Round-2 USER 消息。"""
    return "\n".join(
        [
            "Mode: r2va",
            f"Duration hint: {float(duration):g} seconds",
            "",
            "Short intent:",
            intent.strip(),
            "",
            "Round-1 perception and fusion:",
            round1_output.strip(),
        ]
    )


def r2_format_user(
    intent: str,
    *,
    mode: str,
    duration: float | int,
    upstream: str,
) -> str:
    """构造非 r2va 终轮（Round-2）USER 消息。"""
    fl_hint = ""
    if mode in ("fl2va", "l2va"):
        fl_hint = (
            f"If MODE is fl2va or l2va, the alignment line MUST use S.SS = {float(duration):.2f}."
        )
    return "\n".join(
        [
            f"MODE={mode}",
            f"Duration hint: {float(duration):g} seconds. {fl_hint}".strip(),
            "Serialize upstream content into MiniMax-H3 fields for this MODE.",
            "",
            "Original short intent:",
            intent.strip(),
            "",
            "Upstream rounds:",
            upstream.strip(),
        ]
    )


def r3_format_r2va_user(
    intent: str,
    round1_output: str,
    round2_output: str,
    *,
    duration: float | int,
) -> str:
    """构造 r2va Round-3 USER 消息。"""
    return "\n".join(
        [
            "MODE=r2va",
            f"Duration hint: {float(duration):g} seconds",
            "Enrich and serialize into the six r2va sections. Do not violate user intent or Round-1/2 facts.",
            "",
            "Original short intent:",
            intent.strip(),
            "",
            "Round-1 perception and fusion:",
            round1_output.strip(),
            "",
            "Round-2 scene note:",
            round2_output.strip(),
        ]
    )


def compose_format_system(stem: str, mode: str) -> str:
    """格式轮 SYSTEM = _core + 阶段提示词 + 语法规则卡 + 单个官方示例。"""
    overlay = load_round_system(stem, with_core=True)
    skills = load_skills(format_skill_set_name(mode))
    example = load_example(mode)
    return "\n".join(
        [
            overlay.rstrip(),
            "",
            "--- H3 syntax skills (binding) ---",
            "",
            skills.rstrip(),
            "",
            "--- Official example (format reference only) ---",
            "",
            example.rstrip(),
            "",
        ]
    )


def compose_plan_system(stem: str, mode: str, *, stage: str = "") -> str:
    """规划轮 SYSTEM = 阶段提示词 + 规划侧语法规则卡（含 _core 分层优先级）。"""
    overlay = load_round_system(stem, with_core=True)
    skills = load_skills(plan_skill_set_name(mode, stage=stage or stem))
    return "\n".join(
        [
            overlay.rstrip(),
            "",
            "--- H3 syntax skills (planning subset, binding) ---",
            "",
            skills.rstrip(),
            "",
        ]
    )


def load_stage_prompt(stem: str, *, with_core: bool = False) -> str:
    """读取阶段 SYSTEM 提示词（可选前置 _core）。兼容旧调用；新链路请用 compose_*。"""
    return load_round_system(stem, with_core=with_core)


# 保留符号供测试/外部引用；格式轮默认不再注入全文指南。
__all__ = [
    "compose_format_system",
    "compose_plan_system",
    "load_official_guide",
    "load_stage_prompt",
    "production_block",
    "r1_image_user",
    "r1_keyinfo_user",
    "r1_r2va_user",
    "r2_format_user",
    "r2_plot_user",
    "r3_format_r2va_user",
    "_asset_label_display",
]
