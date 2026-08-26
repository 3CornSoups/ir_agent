"""提示词质量校验：确定性规则硬校验 + 可选 LLM 修复。

规则层不产生额外 HTTP 调用；只有检测到 error 且调用方提供 chat 时才触发
LLM 修复（stage="verify"），修复后重新校验，最多 max_fix_rounds 轮。

各规则对应的质量问题：
- 字段结构 / 对齐句 / 画幅残留  → 结构不稳
- 时间戳单调性                 → 画面诡异（时序混乱）
- 标签编号 / 标签使用          → 丢失参考素材
- <d>[Language] 语言匹配       → 台词发音紊乱
- 禁止 [Mandarin]              → 台词发音紊乱
- 用户原句必须进 <d>           → 台词被翻译 / 漏句
- 未授权多镜 [Shot 2+]         → base prompt 未要求分镜却切镜
- 未授权 non_diegetic_music=N/A → base prompt 未要求无配乐却写成 N/A
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .config import load_prompt

BASE_FIELDS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")
R2VA_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
KEYFRAME_MODES = ("i2va", "fl2va", "l2va")

_ALIGN_PREFIXES = (
    "For the target video, at 0.00 seconds",
    "How the reference pictures align with the target video",
)
_SHOT_TS_RE = re.compile(r"\[Shot\s+(\d+)\]\s*At\s+(\d{2}):(\d{2})\.(\d{3})", re.I)
# Subject 也纳入标签定义匹配：subject_definitions 行首定义以 <Subject N> 开头。
_LABEL_RE = re.compile(r"<(Subject|Picture|Video|Audio)\s+(\d+)>", re.I)
_DLANG_RE = re.compile(r"<d>\s*\[([A-Za-z]+)\]\s*(.*?)</d>", re.S)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")
_ARABIC_RE = re.compile(r"[\u0600-\u06ff]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# 需要与内容文字一一对应的非拉丁语言标签。
# 中文只认 chinese；mandarin 走禁止名单，不能当合法标签。
_SCRIPT_LANGS = {
    "chinese": _CJK_RE,
    "japanese": _KANA_RE,
    "korean": _HANGUL_RE,
    "arabic": _ARABIC_RE,
    "russian": _CYRILLIC_RE,
}
# 拉丁文字语言标签。
_LATIN_LANGS = {"english", "french", "german", "italian", "portuguese", "spanish"}
# H3 对白标签禁止项：必须写成 [Chinese]，不能写 Mandarin。
_FORBIDDEN_DLANG = {"mandarin", "putonghua"}

_QUOTE_RES = (
    re.compile(r"「([^」]+)」"),
    re.compile(r"『([^』]+)』"),
    re.compile(r"“([^”]+)”"),
    re.compile(r'"([^"]+)"'),
)
_PUNCT_NORM = str.maketrans(
    {
        "。": ".",
        "．": ".",
        "！": "!",
        "？": "?",
        "，": ",",
        "、": ",",
        "：": ":",
        "；": ";",
        "…": ".",
        "—": "-",
        "～": "~",
        "「": "",
        "」": "",
        "『": "",
        "』": "",
        '"': "",
        "“": "",
        "”": "",
    }
)

# 画幅/分辨率/帧率残留（strip_canvas 已先清理，这里是最终安全网）。
_CANVAS_RE = re.compile(
    r"(?i)(?<![x-])\b(?:aspect\s+ratio|canvas\s+size|resolution|frame\s+rate|fps|帧率|分辨率|画幅|像素)\b"
    r"|\b(?:768P|2K|1280x720|1920x1080)\b"
    r"|\b(?:16:9|9:16|21:9|4:3|3:4|1:1)\s*(?:aspect\s*ratio|横屏|竖屏)?"
)

# base prompt 明确要求多镜 / 分镜时才放行 [Shot 2+]。
# 刻意不匹配「镜头缓推 / 镜头推进」等单镜运镜用语。
_MULTI_SHOT_INTENT_RE = re.compile(
    r"(?:"
    r"分镜|多镜头|多镜切换|切镜|硬切|镜头切换|切换镜头|蒙太奇|分镜表|分镜脚本|"
    r"第[一二三四五六七八九十两\d]+\s*个?\s*镜头|"
    r"(?:两个|三个|多个)\s*镜头|"
    r"镜头\s*[2-9一二三四五六七八九十]|"
    r"\[?\s*Shot\s*[2-9]\s*\]?|"
    r"multi[\s\-]?shots?|multiple\s+shots?|story\s*board|storyboard|montage|"
    r"shot\s+list|cuts?\s+between|then\s+cut(?:s|\s+to)|cut\s+to\s+(?:a\s+)?(?:close|wide|medium)"
    r")",
    re.I,
)
_SHOT_INDEX_RE = re.compile(r"\[Shot\s+(\d+)\]", re.I)

# base prompt 明确要求 non_diegetic_music = N/A 时才放行。
_NA_MUSIC_INTENT_RE = re.compile(
    r"(?:"
    r"non[_\s\-]?diegetic[_\s\-]?music\s*[:=]\s*N\s*/?\s*A|"
    r"(?:无|不要|无需|不需要|没有|别加|别要)\s*(?:非叙事)?(?:配乐|背景音乐|背景乐|音乐|BGM|bgm)|"
    r"(?:纯|只要|仅|仅要)\s*环境音|"
    r"ambience[\s\-]?only|ambient[\s\-]?only|"
    r"no\s+(?:non[\s\-]?diegetic\s+)?(?:music|score|bgm)|"
    r"without\s+(?:non[\s\-]?diegetic\s+)?(?:music|score|bgm)|"
    r"no\s+score|"
    r"(?:music|score|bgm)\s*[:=]\s*N\s*/?\s*A"
    r")",
    re.I,
)
_MUSIC_FIELD_RE = re.compile(
    r"non_diegetic_music\s*:\s*(.*?)(?="
    r"\n(?:integrated_multimodal_description|overall_soundscape|subject_definitions|"
    r"summary|retention_analysis|detailed_description)\s*:|\Z)",
    re.S | re.I,
)
_MUSIC_NA_RE = re.compile(r"^N\s*/?\s*A\.?\s*$", re.I)


@dataclass(frozen=True)
class VerifyIssue:
    """一条校验问题。severity: error 阻断 / warning 提示。"""

    code: str
    severity: str
    message: str


def intent_allows_multi_shot(intent: str) -> bool:
    """base prompt / 短意图是否明确要求分镜或多镜头。

    未命中时默认单镜头；「镜头缓推」等运镜用语不算多镜授权。
    """
    return bool(_MULTI_SHOT_INTENT_RE.search(intent or ""))


def intent_allows_na_music(intent: str) -> bool:
    """base prompt 是否明确要求 non_diegetic_music 为 N/A / 无配乐。

    未命中时默认必须写具体配乐，不能写 N/A。
    """
    return bool(_NA_MUSIC_INTENT_RE.search(intent or ""))


def extract_non_diegetic_music(prompt: str) -> str:
    """取出 non_diegetic_music 字段正文（去首尾空白）。"""
    m = _MUSIC_FIELD_RE.search(prompt or "")
    return (m.group(1) if m else "").strip()


def check_unauthorized_multi_shot(prompt: str, intent: str) -> list[VerifyIssue]:
    """未要求分镜时若出现 [Shot 2+]，报 error 以便自动修复压回单镜。"""
    if intent_allows_multi_shot(intent):
        return []
    nums = [int(n) for n in _SHOT_INDEX_RE.findall(prompt or "")]
    extra = sorted({n for n in nums if n >= 2})
    if not extra:
        return []
    shown = ", ".join(f"[Shot {n}]" for n in extra)
    return [
        VerifyIssue(
            "unauthorized_multi_shot",
            "error",
            f"base prompt 未明确要求分镜/多镜头/切镜，但提示词出现了 {shown}；"
            "应压成单一连续 [Shot 1]",
        )
    ]


def check_unauthorized_na_music(prompt: str, intent: str) -> list[VerifyIssue]:
    """未要求无配乐时若 non_diegetic_music 为 N/A，报 error 以便自动补写配乐。"""
    if intent_allows_na_music(intent):
        return []
    body = extract_non_diegetic_music(prompt)
    if not body:
        return []
    if not _MUSIC_NA_RE.match(body):
        return []
    return [
        VerifyIssue(
            "unauthorized_na_music",
            "error",
            "base prompt 未明确要求 non_diegetic_music 为 N/A / 无配乐，"
            "但字段写成了 N/A；应写具体乐器与速度节奏",
        )
    ]


def check_field_structure(mode: str, prompt: str) -> list[VerifyIssue]:
    """三字段/六段必须存在且顺序正确。"""
    text = (prompt or "").strip()
    fields = R2VA_FIELDS if mode == "r2va" else BASE_FIELDS
    issues: list[VerifyIssue] = []
    positions: list[int] = []
    for f in fields:
        idx = text.find(f + ":")
        positions.append(idx)
        if idx == -1:
            issues.append(VerifyIssue("field_missing", "error", f"缺少字段: {f}"))
    if all(p >= 0 for p in positions) and positions != sorted(positions):
        issues.append(VerifyIssue("field_order", "error", "字段顺序不符合官方骨架"))
    return issues


def check_alignment_line(mode: str, prompt: str, duration: int) -> list[VerifyIssue]:
    """关键帧模式：首行必须是对齐句，且 S.SS 与时长一致（两位小数）。"""
    if mode not in KEYFRAME_MODES:
        return []
    text = (prompt or "").strip()
    first_line = text.splitlines()[0] if text.splitlines() else ""
    issues: list[VerifyIssue] = []
    if not first_line.startswith(_ALIGN_PREFIXES):
        issues.append(
            VerifyIssue("align_missing", "error", "首行不是官方对齐句（For the target video... / How the reference pictures align...）")
        )
        return issues
    if mode == "i2va":
        return issues
    sss = f"{float(duration):.2f}"
    if f"{sss}-second" not in first_line:
        issues.append(
            VerifyIssue("align_duration", "error", f"对齐句 S.SS 应为 {sss}，与出片时长 {duration}s 一致")
        )
    return issues


def check_timestamps(prompt: str, duration: int) -> list[VerifyIssue]:
    """[Shot N] At MM:SS.mmm 必须严格递增且不超过视频时长。"""
    issues: list[VerifyIssue] = []
    prev_secs = -1.0
    for shot_no, mm, ss, mmm in _SHOT_TS_RE.findall(prompt or ""):
        secs = float(mm) * 60 + float(ss) + float(mmm) / 1000
        if secs <= prev_secs:
            issues.append(
                VerifyIssue(
                    "shot_time_not_increasing",
                    "error",
                    f"[Shot {shot_no}] 时间戳 {mm}:{ss}.{mmm} 未严格递增（前一个时间点为 {prev_secs:.3f}s）",
                )
            )
        if secs > duration + 0.001:
            issues.append(
                VerifyIssue(
                    "shot_time_over_duration",
                    "error",
                    f"[Shot {shot_no}] 时间戳 {mm}:{ss}.{mmm} 超过时长 {duration}s",
                )
            )
        prev_secs = secs
    return issues


def check_label_numbers(
    prompt: str,
    *,
    images: int = 0,
    videos: int = 0,
    audios: int = 0,
) -> list[VerifyIssue]:
    """参考标签编号不能超过实际素材数量（防止发明不存在的素材）。"""
    issues: list[VerifyIssue] = []
    for kind, limit in (("Picture", images), ("Video", videos), ("Audio", audios)):
        for n in {int(num) for k, num in _LABEL_RE.findall(prompt or "") if k == kind}:
            if n > limit:
                issues.append(
                    VerifyIssue(
                        "label_overrun",
                        "error",
                        f"<{kind} {n}> 超出实际素材数（{limit}），不要发明未上传的素材",
                    )
                )
    return issues


def check_label_usage(prompt: str) -> list[VerifyIssue]:
    """r2va：subject_definitions 里「行首独立定义」的标签必须在正文被引用。

    只有行首以 <Subject N> / <Picture N> / <Video N> / <Audio N> 开头的行才视为
    独立定义；定义行内嵌的素材来源引用（如 <Subject 1> is ... in <Picture 1>）
    只说明出处，不单独算作定义，不参与该检查。
    """
    text = (prompt or "").strip()
    m = re.search(r"subject_definitions:\s*(.*?)(?=\n\w+:|$)", text, re.S)
    if not m:
        return []
    defined: set[str] = set()
    for line in m.group(1).splitlines():
        lbl = _LABEL_RE.match(line.strip())
        if lbl:
            defined.add(f"<{lbl.group(1)} {lbl.group(2)}>")
    body = text[m.end() :]
    return [
        VerifyIssue("label_unused", "warning", f"subject_definitions 定义了但正文未引用: {lbl}")
        for lbl in sorted(defined)
        if lbl not in body
    ]


def extract_locked_dialogue(intent: str) -> list[str]:
    """从用户意图的引号里抽出锁定台词，去重且保持出现顺序。"""
    hits: list[tuple[int, str]] = []
    for rx in _QUOTE_RES:
        for match in rx.finditer(intent or ""):
            line = (match.group(1) or "").strip()
            if _is_lockable_line(line):
                hits.append((match.start(), line))
    hits.sort(key=lambda item: item[0])
    seen: set[str] = set()
    lines: list[str] = []
    for _, line in hits:
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return lines


def _is_lockable_line(line: str) -> bool:
    """过滤路径、空串、纯标点，保留对白。"""
    text = (line or "").strip()
    if len(text) < 1:
        return False
    if re.search(r"\.(png|jpg|jpeg|webp|mp4|mov|wav)\b", text, re.I):
        return False
    if not (_CJK_RE.search(text) or _LATIN_RE.search(text)):
        return False
    return True


def _norm_dialogue(text: str) -> str:
    """对白比对用：去掉空白、统一中英文标点。"""
    compact = re.sub(r"\s+", "", (text or "").strip())
    compact = compact.translate(_PUNCT_NORM)
    return compact.replace("...", ".")


def check_forbidden_dialogue_tag(prompt: str) -> list[VerifyIssue]:
    """禁止 [Mandarin] / [Putonghua]；中文必须写 [Chinese]。"""
    issues: list[VerifyIssue] = []
    for lang, content in _DLANG_RE.findall(prompt or ""):
        key = lang.strip().lower()
        if key not in _FORBIDDEN_DLANG:
            continue
        snippet = (content or "").strip()[:30].replace("\n", " ")
        issues.append(
            VerifyIssue(
                "dialogue_forbidden_lang",
                "error",
                f"<d>[{lang}] 禁止使用，中文对白必须写成 [Chinese]: 「{snippet}」",
            )
        )
    return issues


def check_dialogue_verbatim(prompt: str, locked: list[str]) -> list[VerifyIssue]:
    """用户意图里的锁定台词必须原句出现在某个 <d> 内，不得翻译或漏写。"""
    if not locked:
        return []
    inners = [c.strip() for _, c in _DLANG_RE.findall(prompt or "")]
    joined = "\n".join(_norm_dialogue(x) for x in inners)
    issues: list[VerifyIssue] = []
    for line in locked:
        needle = _norm_dialogue(line)
        if needle and needle in joined:
            continue
        issues.append(
            VerifyIssue(
                "dialogue_verbatim_missing",
                "error",
                f"用户原句未出现在 <d> 内（禁止翻译或漏写）: 「{line}」",
            )
        )
    return issues


def check_dialogue_language(prompt: str) -> list[VerifyIssue]:
    """<d>[Lang] 内容</d>：语言标签必须与内容实际文字匹配。"""
    issues: list[VerifyIssue] = []
    for lang, content in _DLANG_RE.findall(prompt or ""):
        key = lang.strip().lower()
        content = content.strip()
        if not content:
            continue
        if key in _SCRIPT_LANGS:
            if not _SCRIPT_LANGS[key].search(content):
                snippet = content[:30].replace("\n", " ")
                issues.append(
                    VerifyIssue(
                        "dialogue_lang_mismatch",
                        "error",
                        f"<d>[{lang}] 内容与语言标签不符: 「{snippet}...」",
                    )
                )
        elif key in _LATIN_LANGS:
            if _CJK_RE.search(content) and not _LATIN_RE.search(content):
                snippet = content[:30].replace("\n", " ")
                issues.append(
                    VerifyIssue(
                        "dialogue_lang_mismatch",
                        "error",
                        f"<d>[{lang}] 内容是中文但标签为 {lang}: 「{snippet}...」",
                    )
                )
    return issues


def check_canvas_residue(prompt: str) -> list[VerifyIssue]:
    """画幅/分辨率/帧率等生产参数不应出现在提示词里。"""
    matches = [m.group(0) for m in _CANVAS_RE.finditer(prompt or "")]
    return [
        VerifyIssue("canvas_residue", "warning", f"残留画幅/分辨率/帧率词: {m}")
        for m in matches
    ]


def verify_prompt(
    mode: str,
    prompt: str,
    *,
    duration: int,
    images: int = 0,
    videos: int = 0,
    audios: int = 0,
    intent: str = "",
) -> list[VerifyIssue]:
    """跑全部规则校验，返回问题列表。"""
    issues: list[VerifyIssue] = []
    issues += check_field_structure(mode, prompt)
    issues += check_alignment_line(mode, prompt, duration)
    issues += check_timestamps(prompt, duration)
    issues += check_label_numbers(prompt, images=images, videos=videos, audios=audios)
    if mode == "r2va":
        issues += check_label_usage(prompt)
    issues += check_forbidden_dialogue_tag(prompt)
    issues += check_dialogue_language(prompt)
    issues += check_dialogue_verbatim(prompt, extract_locked_dialogue(intent))
    issues += check_canvas_residue(prompt)
    issues += check_unauthorized_multi_shot(prompt, intent)
    issues += check_unauthorized_na_music(prompt, intent)
    return issues


def check_intent_with_llm(
    chat: Callable[..., str],
    intent: str,
    prompt: str,
    inventory: str | None,
) -> list[VerifyIssue]:
    """LLM 判断最终提示词是否偏离原始意图（--verify-intent-llm 时启用）。"""
    system = load_prompt("verify_intent")
    user_lines = ["Original intent:", (intent or "").strip()]
    if inventory:
        user_lines.extend(["", "Reference inventory (excerpt):", inventory.strip()[:2000]])
    user_lines.extend(["", "Final prompt:", (prompt or "").strip()])
    raw = chat(system, "\n".join(user_lines), stage="verify_intent")
    try:
        payload = json.loads(raw.strip())
        if not isinstance(payload, dict):
            raise ValueError("非 JSON 对象")
        if not payload.get("consistent", True):
            problems = payload.get("problems") or []
            if not isinstance(problems, list):
                problems = []
            return [
                VerifyIssue("intent_drift", "error", f"意图偏差: {p}")
                for p in problems
                if isinstance(p, str)
            ]
        return []
    except (json.JSONDecodeError, ValueError):
        # 模型未按 JSON 回复时降级为警告，不阻断。
        return [
            VerifyIssue("intent_check_unparseable", "warning", "意图一致性检查返回无法解析的 JSON")
        ]


def verify_and_fix(
    mode: str,
    prompt: str,
    *,
    duration: int,
    images: int = 0,
    videos: int = 0,
    audios: int = 0,
    chat: Callable[..., str] | None = None,
    intent: str = "",
    inventory: str | None = None,
    check_intent_llm: bool = False,
    max_fix_rounds: int = 1,
) -> dict[str, Any]:
    """校验最终提示词；存在 error 且提供 chat 时自动修复（最多 max_fix_rounds 轮）。

    Returns:
        prompt: 校验/修复后的最终提示词
        fixed: 是否发生过修改
        status: passed / failed
        rounds: 实际修复轮数
        issues: 最终问题列表（dict 化）
        intent_llm: 本次是否启用了 LLM 意图检查
    """
    issues = verify_prompt(
        mode,
        prompt,
        duration=duration,
        images=images,
        videos=videos,
        audios=audios,
        intent=intent,
    )
    if check_intent_llm and chat is not None and (intent or "").strip():
        issues.extend(check_intent_with_llm(chat, intent, prompt, inventory))

    current = prompt
    rounds = 0
    if chat is not None and max_fix_rounds > 0 and any(i.severity == "error" for i in issues):
        system = load_prompt("verify_fix")
        for _ in range(max_fix_rounds):
            user_lines = [
                "Issues:",
                *[f"- [{i.code}] {i.message}" for i in issues],
            ]
            locked = extract_locked_dialogue(intent)
            if locked:
                user_lines.extend(
                    [
                        "",
                        "Locked spoken lines (copy each verbatim into <d>; Chinese uses [Chinese], never [Mandarin]; do not translate):",
                        *[f"- {line}" for line in locked],
                    ]
                )
            user_lines.extend(["", "Prompt:", current])
            fixed = chat(system, "\n".join(user_lines), stage="verify")
            if fixed.strip() == current.strip():
                break  # 模型没有改动，避免死循环
            current = fixed
            rounds += 1
            # 重新收集规则 + 意图问题：修复后不静默丢弃意图偏差，且对修复稿复检。
            issues = verify_prompt(
                mode,
                current,
                duration=duration,
                images=images,
                videos=videos,
                audios=audios,
                intent=intent,
            )
            if check_intent_llm and chat is not None and (intent or "").strip():
                issues.extend(check_intent_with_llm(chat, intent, current, inventory))
            if not any(i.severity == "error" for i in issues):
                break

    status = "passed" if not any(i.severity == "error" for i in issues) else "failed"
    return {
        "prompt": current,
        "fixed": current != prompt,
        "status": status,
        "rounds": rounds,
        "issues": [asdict(i) for i in issues],
        "errors": sum(1 for i in issues if i.severity == "error"),
        "warnings": sum(1 for i in issues if i.severity == "warning"),
        "intent_llm": bool(check_intent_llm),
    }
