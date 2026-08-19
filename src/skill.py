"""官方 h3-prompt-writing skill 的加载与后处理。

工作流对齐 SKILL_en.md：
1. 已由调用方确定 MODE（t2va / i2va / fl2va / l2va / r2va）
2. 基础模式读 references/base-en.txt；全参考读 references/ref-en.txt
3. 严格保留指南中的字段名、章节顺序、标签与时间标记
"""

from __future__ import annotations

import re

from .config import ROOT

SKILL_DIR = ROOT / "h3-prompt-writing"
BASE_MODES = ("t2va", "i2va", "fl2va", "l2va")
KEYFRAME_MODES = ("i2va", "fl2va", "l2va")
ALL_MODES = BASE_MODES + ("r2va",)

# 扩写阶段按官方指南给出的镜头路径（不输出 MiniMax 字段）。
EXPAND_HINTS = {
    "t2va": (
        "T2VA: build the full audiovisual timeline from text. "
        "At [Shot 1] state overall style and initial composition."
    ),
    "i2va": (
        "I2VA: <Picture 1> is the true first frame at 0.00s and belongs to [Shot 1]. "
        "Path: first-frame anchor → action onset → continuous development → result or reaction. "
        "Keep identity, clothing, colors, key objects, and spatial layout from the first frame."
    ),
    "fl2va": (
        "FL2VA: Picture 1 is the opening, Picture 2 is the ending. "
        "Path: first-frame state → observable intermediate changes → "
        "progressively narrowing differences → last-frame state. "
        "Prefer a single continuous shot unless the intent explicitly asks for cuts. "
        "Do not describe two static images; write the motion path that connects them."
    ),
    "l2va": (
        "L2VA: <Picture 1> is the LAST frame and belongs to the final [Shot N], not Shot 1. "
        "Path: plausible preceding state → explicit action and transition → "
        "gradual convergence in the final shot → last-frame landing."
    ),
    "r2va": (
        "Ref2VA: say how each attached asset will be used "
        "(<Subject N> / <Picture N> / <Video N> / <Audio N>). "
        "One Picture may contain a 4-grid or 9-grid; list every subject visible in that picture "
        "instead of keeping only the most salient cell. "
        "Do not invent files. Do not output the six MiniMax sections yet."
    ),
}

_SHOT_RE = re.compile(r"\[Shot\s+(\d+)\]", re.I)
_ALIGN_PREFIXES = (
    "For the target video, at 0.00 seconds",
    "How the reference pictures align with the target video",
)
_LAYOUT_RC_RE = re.compile(r"Layout:\s*(\d+)\s*[x×]\s*(\d+)", re.I)
_LAYOUT_NAMED_RE = re.compile(
    r"Layout:\s*(2\s*[x×]\s*2|3\s*[x×]\s*3|4-grid|9-grid|4-panel|9-panel|four-panel|nine-panel)",
    re.I,
)
_CELL_RE = re.compile(r"(?:cell|panel)\s+(\d+)\s*[,x×]\s*(\d+)", re.I)
_NAMED_GRID_SIZE = {
    "2x2": (2, 2),
    "3x3": (3, 3),
    "4-grid": (2, 2),
    "9-grid": (3, 3),
    "4-panel": (2, 2),
    "9-panel": (3, 3),
    "four-panel": (2, 2),
    "nine-panel": (3, 3),
}

# 感知阶段：先报布局，再按格列出 Picture 里出现的全部 Subject，避免只盯住一格。
GRID_SCAN_INSTRUCTION = """
If an attached still is a 2x2 / 3x3 / 4-grid / 9-grid / comic strip / storyboard sheet, do not lock onto one cell.
For each <Picture N>:
1) Start with Layout: single scene | 2x2 grid | 3x3 grid | 1xN strip | other.
2) If it is a grid/strip, scan left-to-right, top-to-bottom. One note per cell: cell r,c — who/what, clothing, pose, setting.
3) Then list Subjects in this picture: every distinct person, animal, object, costume, scene, or pose identity that actually appears. Same identity across cells = one Subject with per-cell pose/angle deltas. Different identities = separate Subjects, each citing this <Picture N> and the cells.
4) Completeness: a 2x2 needs 4 cell notes; a 3x3 needs 9. Never let the most salient panel stand in for the whole sheet.
""".strip()


def parse_grid_layout(inventory: str) -> tuple[int, int] | None:
    """从库存稿解析宫格行列；不是宫格则返回 None。"""
    text = inventory or ""
    m = _LAYOUT_RC_RE.search(text)
    if m:
        rows, cols = int(m.group(1)), int(m.group(2))
        if rows * cols >= 4:
            return rows, cols
        return None
    named = _LAYOUT_NAMED_RE.search(text)
    if not named:
        return None
    key = re.sub(r"\s+", "", named.group(1).lower().replace("×", "x"))
    return _NAMED_GRID_SIZE.get(key)


def grid_coverage_gap(inventory: str) -> str | None:
    """若声明了四宫格/九宫格但格子笔记不足，返回补扫说明。"""
    layout = parse_grid_layout(inventory)
    if not layout:
        return None
    rows, cols = layout
    expected = rows * cols
    cells = {(int(r), int(c)) for r, c in _CELL_RE.findall(inventory or "")}
    if len(cells) >= expected:
        return None
    return (
        f"Layout was {rows}x{cols} ({expected} cells) but only {len(cells)} cell notes were written. "
        "Re-scan the picture. Write cell r,c for every cell in reading order. "
        "Then list every Subject that appears in this Picture — do not keep only the most salient sub-image."
    )


def grid_keep_subjects_note(inventory: str | None) -> str | None:
    """库存已标明宫格时，提醒扩写/格式化保留 Picture 内全部 Subject。"""
    text = (inventory or "").strip()
    if not text:
        return None
    if parse_grid_layout(text) or "Subjects in this picture" in text:
        return (
            "This inventory includes a composite/grid Picture. "
            "Keep every listed subject from that picture; do not collapse unread cells into one salient panel."
        )
    return None


def load_official_guide(mode: str) -> str:
    """读取官方英文写作指南：基础模式用 base-en，r2va 用 ref-en。"""
    mode = mode.lower().strip()
    if mode not in ALL_MODES:
        raise ValueError(f"未知模式: {mode}，可选 {', '.join(ALL_MODES)}")
    name = "ref-en.txt" if mode == "r2va" else "base-en.txt"
    path = SKILL_DIR / "references" / name
    if not path.is_file():
        raise FileNotFoundError(f"缺少官方 skill 指南: {path}")
    return path.read_text(encoding="utf-8").strip() + "\n"


def compose_format_system(mode: str, overlay: str) -> str:
    """把 agent 约束 overlay 与官方指南拼成 format 阶段的 SYSTEM。"""
    guide = load_official_guide(mode)
    parts = [
        overlay.rstrip(),
        "",
        "--- Official H3 writing guide (preserve field names, section order, labels, timing) ---",
        "",
        guide.rstrip(),
        "",
    ]
    return "\n".join(parts)


def expand_hint(mode: str) -> str:
    """返回当前模式在扩写阶段应遵循的官方写作路径。"""
    return EXPAND_HINTS[mode.lower().strip()]


def last_shot_index(text: str) -> int:
    """从稿件中取最大 [Shot N]；没有镜头标记时视为 1。"""
    nums = [int(n) for n in _SHOT_RE.findall(text or "")]
    return max(nums) if nums else 1


def alignment_line(mode: str, duration: int, last_shot: int = 1) -> str:
    """按官方 base 指南生成关键帧模式的第一行对齐指令。"""
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


def ensure_alignment_prefix(mode: str, prompt: str, duration: int) -> str:
    """关键帧模式：用规范对齐句替换或补上稿件首行；其它模式原样返回。"""
    text = (prompt or "").strip()
    mode = mode.lower().strip()
    if mode not in KEYFRAME_MODES:
        return text + ("\n" if text else "")
    n = last_shot_index(text)
    wanted = alignment_line(mode, duration, last_shot=n)
    body = text
    first_line, sep, rest = body.partition("\n")
    if sep and first_line.startswith(_ALIGN_PREFIXES):
        body = rest.lstrip()
    elif first_line.startswith(_ALIGN_PREFIXES):
        body = ""
    if body:
        return wanted + "\n\n" + body.rstrip() + "\n"
    return wanted + "\n"
