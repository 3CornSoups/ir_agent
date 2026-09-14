"""终轮提示词后处理：对齐句补全、画幅清理、本地语法验收。"""

from __future__ import annotations

import logging
import re
from typing import Any

from .config import KEYFRAME_MODES

logger = logging.getLogger(__name__)

# 仅匹配带制作参数关键词的画幅/分辨率/帧率，避免误伤台词与屏上文字。
CANVAS_RE = re.compile(
    r"(?:,\s*)?(?:aspect ratio|canvas size|分辨率|帧率|画幅)\s*[:=为是]?\s*"
    r"(?:16:9|9:16|21:9|4:3|3:4|360P|480P|540P|720P|768P|1080P|2K|4K|"
    r"\d+x\d+|\d+(?:\.\d+)?\s*fps)|"
    r"(?:16:9|9:16|21:9|4:3|3:4)[ \t]*(?:aspect ratio|横屏|竖屏)|"
    r"(?:aspect ratio|canvas size|分辨率|帧率|画幅)[ \t]*"
    r"(?:360P|480P|540P|720P|768P|1080P|2K|4K|1280x720|1920x1080|"
    r"\d+(?:\.\d+)?[ \t]*fps)\b|"
    r"\b\d+(?:\.\d+)?[ \t]*fps\b",
    re.I,
)
_SHOT_RE = re.compile(r"\[Shot\s+(\d+)\]", re.I)
_D_TAG_RE = re.compile(r"<d\b[^>]*>.*?</d>", re.I | re.S)
_QUOTED_RE = re.compile(r'"[^"\n]{1,200}"')
_ALIGN_PREFIXES = (
    "For the target video, at 0.00 seconds",
    "How the reference pictures align with the target video",
)
_CUT_PHRASES = (
    "the camera cuts to",
    "the shot cuts to",
    "the shot transitions to",
    "the shot changes to",
    "the shot switches to",
)
_RETENTION_VISUAL = {
    "fully_preserved",
    "partially_preserved",
    "attribute_transfer",
    "weak_reference",
}
_RETENTION_AUDIO = {
    "fully_copy",
    "partially_copy",
    "reference",
    "weak_reference",
}
_TASK_TYPES = {
    "keyframe completion",
    "reference generation",
    "video editing",
    "video continuation",
    "audio reuse",
    "audio reference",
}
_MOOD_WORDS = re.compile(
    r"\b(emotional|heartfelt|melanchol\w*|uplifting|somber|nostalgic|"
    r"romantic|tragic|joyful|sad|happy|bittersweet)\b",
    re.I,
)
_SPEAKER_RE = re.compile(r"\(S(\d+(?:\s*,\s*S\d+)*)\)")
_SHOT_TIME_RE = re.compile(
    r"\[Shot\s+(\d+)\]\s+At\s+(\d{2}):(\d{2})\.(\d{3})\b",
    re.I,
)
_FIELD_ORDER_BASE = (
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)
_FIELD_ORDER_R2VA = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)


def strip_canvas(text: str) -> str:
    """去掉误写入字段的画幅/分辨率/帧率，保留段落换行与 <d>/引号内原文。"""
    placeholders: list[str] = []

    def _hold(match: re.Match[str]) -> str:
        placeholders.append(match.group(0))
        return f"\x00PH{len(placeholders) - 1}\x00"

    protected = _D_TAG_RE.sub(_hold, text or "")
    protected = _QUOTED_RE.sub(_hold, protected)
    cleaned = CANVAS_RE.sub(" ", protected)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n[ \t]+", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    for i, original in enumerate(placeholders):
        cleaned = cleaned.replace(f"\x00PH{i}\x00", original)
    return cleaned.strip() + "\n"


def last_shot_index(text: str) -> int:
    """从稿件中取最大 [Shot N]；没有镜头标记时视为 1。"""
    nums = [int(n) for n in _SHOT_RE.findall(text or "")]
    return max(nums) if nums else 1


def alignment_line(mode: str, duration: int | float, last_shot: int = 1) -> str:
    """按官方指南生成关键帧模式的第一行对齐指令。"""
    mode = mode.lower().strip()
    sss = f"{float(duration):.2f}"
    n = max(1, int(last_shot))
    if mode == "i2va":
        return (
            "For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced."
        )
    if mode == "fl2va":
        return (
            "How the reference pictures align with the target video — "
            "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
            f"Picture 2 (from Shot {n}) aligns with the {sss}-second mark of the target video."
        )
    if mode == "l2va":
        return (
            "How the reference pictures align with the target video — "
            f"<Picture 1> (from [Shot {n}]) aligns with the {sss}-second mark of the target video."
        )
    raise ValueError(f"{mode} 不是关键帧模式，无对齐句")


def _looks_like_alignment(line: str) -> bool:
    """判断一行是否像关键帧对齐指令（放宽措辞差异）。"""
    s = (line or "").strip()
    if not s:
        return False
    if s.startswith(_ALIGN_PREFIXES):
        return True
    low = s.lower()
    if low.startswith("for the target video"):
        return True
    if "align" in low and "target video" in low:
        return True
    if "fully referenced" in low and "picture" in low:
        return True
    return False


def ensure_alignment_prefix(mode: str, prompt: str, duration: int | float) -> str:
    """关键帧模式：剥离旧对齐句后按正文镜号补规范首行。"""
    text = (prompt or "").strip()
    mode = mode.lower().strip()
    if mode not in KEYFRAME_MODES:
        return text + ("\n" if text else "")

    body = text
    # 连续剥掉开头多行对齐句（含措辞漂移）
    while body:
        first_line, sep, rest = body.partition("\n")
        if _looks_like_alignment(first_line):
            body = rest.lstrip("\n") if sep else ""
            continue
        break

    n = last_shot_index(body)
    wanted = alignment_line(mode, duration, last_shot=n)
    if body:
        return wanted + "\n\n" + body.rstrip() + "\n"
    return wanted + "\n"


def clean_final_prompt(prompt: str, mode: str, duration: int | float) -> str:
    """终轮输出清洗：去 MODE= 前缀、markdown 围栏、补对齐句、清画幅。"""
    text = (prompt or "").strip()
    text = re.sub(r"^MODE=[a-z0-9_]+[ \t]*\n*", "", text, flags=re.I).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    text = ensure_alignment_prefix(mode, text, duration)
    return strip_canvas(text)


def _issue(severity: str, code: str, message: str) -> dict[str, str]:
    """构造一条验收问题。"""
    return {"severity": severity, "code": code, "message": message}


def _extract_cast_ids(upstream: str) -> set[str]:
    """从上游 [CAST] 段提取说话人编号集合，如 {'1','2'}。"""
    text = upstream or ""
    m = re.search(r"\[CAST\](.*?)(?:\n\[|\Z)", text, re.I | re.S)
    block = m.group(1) if m else text
    ids: set[str] = set()
    for match in re.finditer(r"\(S(\d+)\)", block):
        ids.add(match.group(1))
    return ids


def _count_upstream_dialogue_lines(upstream: str) -> int | None:
    """统计上游归属台词条数；禁言返回 0；无法判断返回 None。"""
    text = upstream or ""
    if re.search(r"dialogue\s*=\s*forbidden", text, re.I):
        return 0
    m = re.search(r"\[CAST\](.*?)(?:\n\[|\Z)", text, re.I | re.S)
    block = m.group(1) if m else ""
    if not block.strip():
        return None
    lines = re.findall(r"\(S\d+(?:\s*,\s*S\d+)*\)\s*:\s*[\"'].+?[\"']", block)
    if lines:
        return len(lines)
    # 兼容无引号的 (S1): 文本
    lines2 = re.findall(r"\(S\d+(?:\s*,\s*S\d+)*\)\s*:\s*\S+", block)
    return len(lines2) if lines2 else None


def verify_local(
    prompt: str,
    *,
    mode: str,
    duration: float | int,
    upstream: str = "",
) -> dict[str, Any]:
    """零成本语法验收：只告警，不改稿。"""
    mode = mode.lower().strip()
    text = prompt or ""
    issues: list[dict[str, str]] = []
    dur = float(duration)

    # --- 字段 ---
    fields = _FIELD_ORDER_R2VA if mode == "r2va" else _FIELD_ORDER_BASE
    positions: list[tuple[str, int]] = []
    for name in fields:
        pat = re.compile(rf"^{re.escape(name)}\s*:", re.I | re.M)
        m = pat.search(text)
        if not m:
            issues.append(_issue("error", "field_missing", f"缺少字段 {name}"))
        else:
            positions.append((name, m.start()))
    if len(positions) == len(fields):
        ordered = [n for n, _ in sorted(positions, key=lambda x: x[1])]
        if ordered != list(fields):
            issues.append(
                _issue(
                    "error",
                    "field_order",
                    f"字段顺序应为 {' → '.join(fields)}，实际为 {' → '.join(ordered)}",
                )
            )

    # --- 对齐句 ---
    if mode in KEYFRAME_MODES:
        first = text.strip().split("\n", 1)[0] if text.strip() else ""
        wanted_prefix = alignment_line(mode, dur, last_shot=last_shot_index(text))
        # 允许镜号不同，但前缀骨架必须像官方句
        if not _looks_like_alignment(first):
            issues.append(
                _issue(
                    "error",
                    "alignment_missing",
                    f"关键帧模式首行缺少对齐句（期望类似: {wanted_prefix[:80]}…）",
                )
            )

    # --- Shot 时间轴 ---
    if re.search(r"\[Shot\s+1\]\s+At\s+\d", text, re.I):
        issues.append(
            _issue("error", "shot1_has_timestamp", "[Shot 1] 不应带 At MM:SS.mmm 时间戳")
        )
    times: list[tuple[int, float]] = []
    for m in _SHOT_TIME_RE.finditer(text):
        shot_n = int(m.group(1))
        secs = int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000.0
        times.append((shot_n, secs))
        if secs > dur + 0.05:
            issues.append(
                _issue(
                    "error",
                    "shot_time_out_of_range",
                    f"[Shot {shot_n}] 切点 {secs:.3f}s 超出时长 {dur:g}s",
                )
            )
    prev = -1.0
    for shot_n, secs in times:
        if secs <= prev:
            issues.append(
                _issue(
                    "error",
                    "shot_time_not_increasing",
                    f"[Shot {shot_n}] 切点 {secs:.3f}s 未严格递增",
                )
            )
            break
        prev = secs

    # --- 硬切措辞（粗检：含 "cuts to" 但不在白名单）---
    for m in re.finditer(r"([^.!\n]{0,40}\bcuts?\s+to\b[^.!\n]{0,40})", text, re.I):
        frag = m.group(1).strip()
        low = frag.lower()
        if not any(p in low for p in _CUT_PHRASES):
            # 允许 "final words carry over" 等非切镜
            if "cut to" in low or "cuts to" in low:
                if not any(
                    p.split("cuts to")[-1].strip() in low
                    or p in low
                    for p in _CUT_PHRASES
                ):
                    # 更严：要求完整短语出现在附近
                    window = text[max(0, m.start() - 30) : m.end() + 30].lower()
                    if not any(p in window for p in _CUT_PHRASES):
                        issues.append(
                            _issue(
                                "warning",
                                "cut_phrase_invalid",
                                f"切镜措辞疑似不在官方五选一内: …{frag}…",
                            )
                        )

    # --- 对白 / 说话人 ---
    d_tags = list(_D_TAG_RE.finditer(text))
    upstream_forbidden = bool(
        re.search(r"dialogue\s*=\s*forbidden", upstream or "", re.I)
    )
    if upstream_forbidden and d_tags:
        issues.append(
            _issue(
                "error",
                "dialogue_forbidden_violated",
                "上游标记 dialogue=forbidden，但终稿出现 <d>",
            )
        )
    expected_lines = _count_upstream_dialogue_lines(upstream)
    if expected_lines is not None and expected_lines > 0 and len(d_tags) < expected_lines:
        issues.append(
            _issue(
                "warning",
                "dialogue_missing",
                f"上游约有 {expected_lines} 句归属台词，终稿仅 {len(d_tags)} 个 <d>",
            )
        )

    cast_ids = _extract_cast_ids(upstream)
    for m in d_tags:
        pre = text[max(0, m.start() - 120) : m.start()]
        if not _SPEAKER_RE.search(pre):
            issues.append(
                _issue(
                    "error",
                    "speaker_id_missing",
                    f"<d> 前缺少 (Sx) 说话人标记: …{text[m.start(): m.start()+40]}…",
                )
            )
        else:
            for sm in _SPEAKER_RE.finditer(pre):
                for part in re.split(r"\s*,\s*", sm.group(1)):
                    num = part.replace("S", "").replace("s", "").strip()
                    if cast_ids and num not in cast_ids:
                        issues.append(
                            _issue(
                                "warning",
                                "speaker_id_unknown",
                                f"使用了 CAST 外的说话人 (S{num})",
                            )
                        )

    if mode == "r2va":
        ret_m = re.search(
            r"retention_analysis\s*:(.*?)(?:\n(?:detailed_description|overall_soundscape)\s*:|\Z)",
            text,
            re.I | re.S,
        )
        if ret_m:
            ret_body = ret_m.group(1)
            if _SPEAKER_RE.search(ret_body):
                issues.append(
                    _issue(
                        "error",
                        "speaker_id_in_retention",
                        "retention_analysis 内禁止出现 (Sx)",
                    )
                )
            for line in ret_body.splitlines():
                if not line.strip() or not re.search(
                    r"<(Subject|Picture|Video|Audio)\s+\d+>", line, re.I
                ):
                    continue
                low = line.lower()
                if re.search(r"<Audio\s+\d+>", line, re.I):
                    if not any(k in low for k in _RETENTION_AUDIO):
                        issues.append(
                            _issue(
                                "warning",
                                "retention_marker_invalid",
                                f"音频保留行缺少官方标记: {line.strip()[:80]}",
                            )
                        )
                else:
                    if not any(k in low for k in _RETENTION_VISUAL):
                        issues.append(
                            _issue(
                                "warning",
                                "retention_marker_invalid",
                                f"视觉保留行缺少官方标记: {line.strip()[:80]}",
                            )
                        )

        sum_m = re.search(
            r"summary\s*:\s*\[([^\]]+)\]",
            text,
            re.I,
        )
        if sum_m:
            parts = [p.strip().lower() for p in sum_m.group(1).split("+")]
            bad = [p for p in parts if p and p not in _TASK_TYPES]
            if bad:
                issues.append(
                    _issue(
                        "error",
                        "task_type_invalid",
                        f"summary task type 非法: {bad}",
                    )
                )
        elif re.search(r"summary\s*:", text, re.I):
            issues.append(
                _issue(
                    "warning",
                    "task_type_invalid",
                    "summary 首行缺少 [task type] 方括号前缀",
                )
            )

        det_m = re.search(
            r"detailed_description\s*:(.*?)(?:\noverall_soundscape\s*:|\Z)",
            text,
            re.I | re.S,
        )
        if det_m:
            words = re.findall(r"[A-Za-z]+", det_m.group(1))
            if len(words) < 350:
                issues.append(
                    _issue(
                        "warning",
                        "density_low",
                        f"detailed_description 约 {len(words)} 词，生成类任务通常 350–500",
                    )
                )

    music_m = re.search(
        r"non_diegetic_music\s*:(.*?)(?:\n[a-z_]+\s*:|\Z)",
        text,
        re.I | re.S,
    )
    if music_m:
        body = music_m.group(1).strip()
        if body.upper() != "N/A" and _MOOD_WORDS.search(body):
            issues.append(
                _issue(
                    "warning",
                    "mood_word_in_music",
                    "non_diegetic_music 出现抽象情绪词，官方要求写配器/速度/节奏/动态",
                )
            )

    errors = sum(1 for i in issues if i["severity"] == "error")
    warnings = sum(1 for i in issues if i["severity"] == "warning")
    status = "ok" if errors == 0 else "fail"
    if issues:
        for item in issues:
            logger.warning(
                "verify_local %s %s: %s",
                item["severity"],
                item["code"],
                item["message"],
            )
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "issues": issues,
        "prompt": prompt,
        "fixed": False,
        "dialogue_check": any(
            i["code"].startswith("dialogue") or i["code"].startswith("speaker")
            for i in issues
        ),
        "rounds": 0,
    }
