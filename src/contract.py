"""Intent Contract：把短意图解析成机器可校验的保真事实来源。

设计约束（见 docs/优化方案-保真优先-v2.md §3.1）：
1. 抽取节点只抽取、不推断不补全；用户没说的保持空。
2. dialogue / onscreen_text 必须原文逐字（含标点）。
3. ambiguities 非空不阻塞管线，仅记录。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .config import load_prompt
from .verify import extract_locked_onscreen

ChatFn = Callable[..., str]

DURATION_RE = re.compile(r"(?:约|大概|大约)?\s*(\d{1,2})\s*秒")
ACTION_CHAIN_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9]{1,12})"
    r"(?:\s*[→\-–—]+\s*|\s*->\s*|\s*→\s*)"
    r"([\u4e00-\u9fffA-Za-z0-9]{1,12}"
    r"(?:\s*[→\-–—]+\s*|\s*->\s*|\s*→\s*"
    r"[\u4e00-\u9fffA-Za-z0-9]{1,12})*)"
)
ARROW_SPLIT_RE = re.compile(r"\s*(?:→|->|—|–|-)\s*")
SLOGAN_RE = re.compile(
    r"(?:口号|标语|花字|字幕|标题|屏上|屏幕文字|CTA|"
    r"写着|旁注|标注|显示|屏显|界面|高亮|胸牌|说明牌|广告牌|绣|大字|提示)"
    r"[^「」\"“”\n]{0,16}[「\"“]([^」\"”]+)[」\"”]"
)
# 邻近窗口内出现这些线索，才把引号当「可能台词」（确定性兜底；有 LLM 时以模型分类为准）
# 线索须贴在引号前（允许冒号/空白）；勿单独匹配「叫」，否则「名叫」会误命中
SPEECH_CUE_RE = re.compile(
    r"(?:"
    r"低语|低声|怒吼|质问|回应|回答|旁白|画外音|独白|心里想|心中想|内心|念出|说出|叫道|短句|"
    r"(?:说|讲|问|喊)(?:道|着)?"
    r"|says?|said|asks?|asked|shouts?|whispers?|voice[- ]?over|\bVO\b"
    r")[：:\s]*$",
    re.I,
)
_QUOTE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"「([^」]+)」"),
    re.compile(r"『([^』]+)』"),
    re.compile(r"“([^”]+)”"),
    re.compile(r'"([^"]+)"'),
)
FORBIDDEN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"禁止切镜|不得切镜|不要切镜|单镜头|一镜到底|不要分镜"), "禁止切镜/单镜头"),
    (re.compile(r"禁止(?:人物|人脸|人)入镜|不得(?:人物|人脸|人)入镜|不要(?:人物|人脸|人)入镜|禁止人物"), "禁止人物/人脸入镜"),
    (re.compile(r"不要配乐|禁止配乐|无配乐|不要音乐|不要非叙境"), "不要配乐"),
    (re.compile(r"不得出现任何文字|不要出现文字|禁止文字|画面不得出现任何文字|无字幕"), "画面不得出现文字"),
    (re.compile(r"不要实拍|禁止实拍|非实拍"), "不要实拍"),
)
STYLE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"纯手绘速写|手绘速写|纯手绘"), "纯手绘速写"),
    (re.compile(r"动漫|二次元|日漫"), "动漫"),
    (re.compile(r"水墨|国风水墨"), "水墨"),
    (re.compile(r"赛博朋克"), "赛博朋克"),
)
NEGATIVE_STYLE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"不要实拍|禁止实拍"), "不要实拍"),
    (re.compile(r"不要动漫|禁止动漫"), "不要动漫"),
)


@dataclass
class DialogueLine:
    """一句对白/旁白：原文逐字 + 语言、说话人与播送方式。

    delivery:
      - spoken: 角色张嘴说出（默认）
      - voiceover: 画外音/旁白，须闭口
      - internal: 内心独白，须闭口且不得写成对口型对白
    """

    text: str
    language: str = ""
    speaker: str = ""
    delivery: str = "spoken"  # spoken|voiceover|internal


@dataclass
class ShotConstraint:
    """镜头约束：是否单镜头、最大镜头数。"""

    single_shot: bool = False
    max_shots: int | None = None


@dataclass
class RefAttr:
    """参考素材上的一条属性（perceive 之后回填）。"""

    ref_tag: str
    attr: str
    kind: str = "identity"  # identity|scene|motion|style


@dataclass
class IntentContract:
    """用户意图契约：保真度的唯一事实来源。"""

    must_elements: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    dialogue: list[DialogueLine] = field(default_factory=list)
    onscreen_text: list[str] = field(default_factory=list)
    shot_constraint: ShotConstraint = field(default_factory=ShotConstraint)
    duration_sec: float = 5.0
    action_chain: list[str] = field(default_factory=list)
    explicit_style: str | None = None
    explicit_negatives: list[str] = field(default_factory=list)
    reference_attrs: list[RefAttr] = field(default_factory=list)
    reference_roles: dict[str, str] = field(default_factory=dict)
    intent_raw: str = ""
    mode: str = "t2va"
    ambiguities: list[str] = field(default_factory=list)
    # unspecified | none（禁烧录字幕/花字）| whitelist（仅 onscreen_text）
    subtitle_policy: str = "unspecified"

    def to_dict(self) -> dict[str, Any]:
        """序列化为可写入 run.json 的字典。"""
        return asdict(self)

    def is_nonempty(self) -> bool:
        """是否抽出了至少一项有意义约束（供 S3 全量抽检）。"""
        if self.must_elements or self.forbidden or self.dialogue or self.onscreen_text:
            return True
        if self.action_chain or self.explicit_style or self.explicit_negatives:
            return True
        if self.shot_constraint.single_shot or self.shot_constraint.max_shots is not None:
            return True
        if self.ambiguities:
            return True
        # 极简意图：至少应有 duration 与原始文本
        return bool((self.intent_raw or "").strip()) and self.duration_sec > 0

    def format_for_prompt(self) -> str:
        """把契约格式化为下游节点可粘贴的 CONTRACT 块。"""
        lines = [
            "=== INTENT CONTRACT (machine-verified; do not invent beyond this) ===",
            f"mode: {self.mode}",
            f"duration_sec: {self.duration_sec}",
            f"must_elements: {json.dumps(self.must_elements, ensure_ascii=False)}",
            f"forbidden: {json.dumps(self.forbidden, ensure_ascii=False)}",
            f"action_chain: {json.dumps(self.action_chain, ensure_ascii=False)}",
            f"explicit_style: {self.explicit_style or ''}",
            f"explicit_negatives: {json.dumps(self.explicit_negatives, ensure_ascii=False)}",
            f"shot_constraint: single_shot={self.shot_constraint.single_shot}, "
            f"max_shots={self.shot_constraint.max_shots}",
            f"subtitle_policy: {self.subtitle_policy}",
        ]
        if self.dialogue:
            lines.append("dialogue (verbatim; bind speaker; honor delivery):")
            for d in self.dialogue:
                meta = f" [{d.language}]" if d.language else ""
                sp = f" speaker={d.speaker}" if d.speaker else ""
                deliv = f" delivery={d.delivery}" if d.delivery and d.delivery != "spoken" else ""
                lines.append(f'  - "{d.text}"{meta}{sp}{deliv}')
            if any(d.delivery in {"voiceover", "internal"} for d in self.dialogue):
                lines.append(
                    "  NOTE: voiceover/internal lines MUST use off-screen voiceover "
                    "and state lips/mouth remain closed (no lip-sync speaking)."
                )
        if self.subtitle_policy == "none":
            lines.append(
                "SUBTITLE POLICY=none: forbid burned-in subtitles/captions/花字; "
                "diegetic signs only if listed in onscreen_text."
            )
        elif self.subtitle_policy == "whitelist" and self.onscreen_text:
            lines.append(
                "SUBTITLE POLICY=whitelist: only the onscreen_text lines below may appear as visible text."
            )
        if self.onscreen_text:
            lines.append("onscreen_text (verbatim):")
            for t in self.onscreen_text:
                lines.append(f'  - "{t}"')
        if self.reference_attrs:
            lines.append("reference_attrs:")
            for a in self.reference_attrs:
                lines.append(f"  - {a.ref_tag} [{a.kind}]: {a.attr}")
        if self.ambiguities:
            lines.append(f"ambiguities: {json.dumps(self.ambiguities, ensure_ascii=False)}")
        lines.append("=== END CONTRACT ===")
        return "\n".join(lines)


def _unique_keep_order(items: list[str]) -> list[str]:
    """去空、去重并保序。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = (item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def extract_duration_sec(intent: str, fallback: float = 5.0) -> float:
    """从短意图抽取时长秒数，夹到 4–15；未写则用 fallback。"""
    m = DURATION_RE.search(intent or "")
    if not m:
        return float(max(4, min(15, fallback)))
    return float(max(4, min(15, int(m.group(1)))))


_ROLE_PREFIX_RE = re.compile(
    r"^(球员|厨师|舞者|骑手|店员|技术员|女孩|老人|她|他|运动员|主角)(.+)$"
)


def _peel_subject_prefix(parts: list[str]) -> list[str]:
    """若首段以常见角色主语开头，剥掉主语保留动作。"""
    if len(parts) < 2:
        return parts
    m = _ROLE_PREFIX_RE.match(parts[0])
    if not m:
        return parts
    action = m.group(2).strip()
    if not action:
        return parts
    return [action, *parts[1:]]


def extract_action_chain(intent: str) -> list[str]:
    """从「A→B→C」或「A->B->C」类写法抽取有序动作链；无箭头则空。"""
    text = intent or ""
    # 优先匹配显式箭头链
    for m in re.finditer(
        r"([\u4e00-\u9fffA-Za-z0-9]{1,16}(?:\s*(?:→|->|—|–)\s*[\u4e00-\u9fffA-Za-z0-9]{1,16}){1,8})",
        text,
    ):
        parts = [p for p in ARROW_SPLIT_RE.split(m.group(1)) if p.strip()]
        if len(parts) >= 2:
            return _unique_keep_order(_peel_subject_prefix(parts))
    return []


def extract_forbidden(intent: str) -> list[str]:
    """用确定性模式抽出否定约束原文片段（不发明）。"""
    text = intent or ""
    hits: list[str] = []
    # 整句级：带「禁止/不要/不得」的短片段
    for m in re.finditer(r"[^，。；;\n]{0,8}(?:禁止|不要|不得|勿)[^，。；;\n]{0,16}", text):
        frag = m.group(0).strip(" ，。；;")
        if frag:
            hits.append(frag)
    for rx, _label in FORBIDDEN_PATTERNS:
        if rx.search(text):
            # 用命中的原文片段，而非标签
            m = rx.search(text)
            if m:
                hits.append(m.group(0))
    return _unique_keep_order(hits)


def extract_shot_constraint(intent: str) -> ShotConstraint:
    """抽取镜头约束：单镜头 / 最大镜头数。"""
    text = intent or ""
    single = bool(
        re.search(r"禁止切镜|不得切镜|不要切镜|单镜头|一镜到底|不要分镜|不得分镜", text)
    )
    max_shots: int | None = 1 if single else None
    m = re.search(r"(?:最多|不超过)\s*(\d+)\s*(?:个)?(?:镜头|切镜)", text)
    if m:
        max_shots = int(m.group(1))
        if max_shots == 1:
            single = True
    return ShotConstraint(single_shot=single, max_shots=max_shots)


def extract_explicit_style(intent: str) -> str | None:
    """抽取用户显式风格；未写则 None。"""
    text = intent or ""
    for rx, label in STYLE_PATTERNS:
        if rx.search(text):
            m = rx.search(text)
            return (m.group(0) if m else label).strip()
    return None


def extract_explicit_negatives(intent: str) -> list[str]:
    """抽取用户显式排除的风格。"""
    text = intent or ""
    hits: list[str] = []
    for rx, _label in NEGATIVE_STYLE_PATTERNS:
        m = rx.search(text)
        if m:
            hits.append(m.group(0))
    return _unique_keep_order(hits)


def extract_onscreen_extended(intent: str) -> list[str]:
    """屏上文字：复用 verify 抽取，并补「口号」类引号。"""
    base = list(extract_locked_onscreen(intent))
    extra: list[str] = []
    for m in SLOGAN_RE.finditer(intent or ""):
        line = (m.group(1) or "").strip()
        if line:
            extra.append(line)
    return _unique_keep_order(base + extra)


def extract_subtitle_policy(intent: str) -> str:
    """抽取字幕策略：none / whitelist / unspecified。"""
    text = intent or ""
    if re.search(
        r"(?:全程|视频全程|整段|全部)?(?:不要|禁止|不得|勿|无)字幕|"
        r"不要花字|禁止花字|无烧录字幕|no\s+subtitles?|without\s+subtitles?",
        text,
        re.I,
    ):
        return "none"
    if re.search(r"(?:字幕|花字|屏上|标题|CTA).{0,8}[「\"“]", text):
        return "whitelist"
    return "unspecified"


def _infer_delivery_near(intent: str, quote_start: int, quote_text: str) -> str:
    """根据引号邻近上下文推断播送方式（只认显式线索）。"""
    left = (intent or "")[max(0, quote_start - 40) : quote_start]
    right = (intent or "")[quote_start : min(len(intent or ""), quote_start + len(quote_text) + 36)]
    window = left + right
    if re.search(r"内心|独白|心里想|心中想|不要说出口|不得说出口|不要张嘴|闭口|别说出来", window):
        return "internal"
    if re.search(r"旁白|画外音|画外|off[- ]?screen|voice[- ]?over|\bVO\b", window, re.I):
        return "voiceover"
    # 全局意图级：整段要求独白勿说出口
    if re.search(r"内心独白|独白不要说|不要把独白说", intent or ""):
        if quote_text and quote_text in (intent or ""):
            # 仅当附近也有独白/内心字样，或整句明确「内心独白「…」」
            if re.search(r"内心|独白", left + "「" + quote_text):
                return "internal"
    return "spoken"


def _infer_speaker_near(intent: str, quote_start: int) -> str:
    """从引号左侧抽取说话人短语；不确定则空串。"""
    left = (intent or "")[max(0, quote_start - 48) : quote_start]
    # 典型：短发女高管尖锐地质问「…」/ 小妖说道：「…」
    m = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9·]{1,16})"
        r"(?:尖锐地|冷冷地|轻轻地|大声|低声)?"
        r"(?:说道|质问|怒吼|回应|回答|低语|叫道|讲道|喊|问|说)"
        r"[：:\s]*$",
        left,
    )
    if m:
        return m.group(1).strip()
    m = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9·]{2,20})[：:\s]*$",
        left,
    )
    if m:
        cand = m.group(1).strip()
        # 过滤纯连接词
        if cand not in {"然后", "接着", "同时", "最后", "以及", "还有", "不要", "禁止"}:
            return cand
    return ""


def extract_quote_spans(intent: str) -> list[tuple[int, str]]:
    """抽出意图里全部引号片段（位置, 原文），按出现顺序去重。"""
    from .verify import _is_lockable_line

    raw = intent or ""
    hits: list[tuple[int, str]] = []
    for rx in _QUOTE_RES:
        for match in rx.finditer(raw):
            text = (match.group(1) or "").strip()
            if text and _is_lockable_line(text):
                hits.append((match.start(), text))
    hits.sort(key=lambda x: x[0])
    seen: set[str] = set()
    out: list[tuple[int, str]] = []
    for start, text in hits:
        if text in seen:
            continue
        seen.add(text)
        out.append((start, text))
    return out


def _has_speech_cue_near(intent: str, quote_start: int) -> bool:
    """引号左侧短窗是否出现说话/旁白/独白线索。"""
    left = (intent or "")[max(0, quote_start - 48) : quote_start]
    return bool(SPEECH_CUE_RE.search(left))


def extract_dialogue_lines(intent: str) -> list[DialogueLine]:
    """对白逐字抽取（含 speaker/delivery）。

    确定性规则：屏上字线索的引号排除；其余仅当邻近有说话线索时才算台词。
    风格名/产品名等裸引号不再默认进对白（有 LLM 时由模型分类覆盖）。
    """
    raw = intent or ""
    onscreen = set(extract_onscreen_extended(raw))
    lines: list[DialogueLine] = []
    for start, text in extract_quote_spans(raw):
        if text in onscreen:
            continue
        if not _has_speech_cue_near(raw, start):
            continue
        lang = "Chinese" if re.search(r"[\u4e00-\u9fff]", text) else ""
        speaker = _infer_speaker_near(raw, start)
        delivery = _infer_delivery_near(raw, start, text)
        lines.append(
            DialogueLine(text=text, language=lang, speaker=speaker, delivery=delivery)
        )
    return lines


def _dialogue_from_meta(
    text: str,
    *,
    intent: str,
    start: int,
    speaker: str = "",
    delivery: str = "",
    language: str = "",
) -> DialogueLine:
    """用邻近启发式补全 speaker/delivery/language。"""
    lang = (language or "").strip()
    if not lang:
        lang = "Chinese" if re.search(r"[\u4e00-\u9fff]", text) else ""
    sp = (speaker or "").strip() or _infer_speaker_near(intent, start)
    dl = (delivery or "").strip().lower()
    if dl not in {"spoken", "voiceover", "internal"}:
        dl = _infer_delivery_near(intent, start, text)
    return DialogueLine(text=text, language=lang, speaker=sp, delivery=dl)


def merge_llm_quote_roles(
    payload: dict[str, Any],
    *,
    intent: str,
    base_onscreen: list[str],
    base_dialogue: list[DialogueLine],
) -> tuple[list[DialogueLine], list[str]]:
    """按 LLM 分类合并引号角色；正文必须是意图里的引号原文。

    支持：
    - quote_labels: [{text, role: spoken|voiceover|internal|onscreen|ignore, speaker?, delivery?}]
    - 或沿用 dialogue[] / onscreen_text[]（列出的引号才算；未列出 = ignore）

    高置信屏上线索（确定性 base_onscreen）始终并入 onscreen，防止漏锁招牌/口号。
    """
    quote_idx = {text: start for start, text in extract_quote_spans(intent)}
    if not quote_idx:
        return [], list(base_onscreen)

    dialogue: list[DialogueLine] = []
    onscreen: list[str] = []
    seen_d: set[str] = set()
    seen_o: set[str] = set()

    def _add_onscreen(text: str) -> None:
        if text in quote_idx and text not in seen_o:
            seen_o.add(text)
            onscreen.append(text)

    def _add_dialogue(
        text: str,
        *,
        speaker: str = "",
        delivery: str = "",
        language: str = "",
    ) -> None:
        if text not in quote_idx or text in seen_o or text in seen_d:
            return
        seen_d.add(text)
        dialogue.append(
            _dialogue_from_meta(
                text,
                intent=intent,
                start=quote_idx[text],
                speaker=speaker,
                delivery=delivery,
                language=language,
            )
        )

    labels = payload.get("quote_labels")
    used_labels = isinstance(labels, list) and bool(labels)
    llm_hit = False
    if used_labels:
        for item in labels:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if text not in quote_idx:
                continue
            role = str(item.get("role") or "ignore").strip().lower()
            if role in {"onscreen", "on_screen", "caption", "subtitle", "signage", "slogan"}:
                before = len(seen_o)
                _add_onscreen(text)
                if len(seen_o) > before:
                    llm_hit = True
            elif role in {"spoken", "voiceover", "internal", "dialogue", "speech"}:
                delivery = role if role in {"spoken", "voiceover", "internal"} else str(
                    item.get("delivery") or "spoken"
                )
                before = len(seen_d)
                _add_dialogue(
                    text,
                    speaker=str(item.get("speaker") or ""),
                    delivery=delivery,
                    language=str(item.get("language") or ""),
                )
                if len(seen_d) > before:
                    llm_hit = True
            elif role in {"ignore", "none", "other", "style", "name"}:
                # 显式 ignore 也算一次有效分类命中（阻止回退把风格名又锁成台词）
                if text in quote_idx:
                    llm_hit = True
            # 其它未知 role：忽略
    else:
        for text in _as_str_list(payload.get("onscreen_text")):
            before = len(seen_o)
            _add_onscreen(text)
            if len(seen_o) > before:
                llm_hit = True
        for item in payload.get("dialogue") or []:
            before = len(seen_d)
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                _add_dialogue(
                    text,
                    speaker=str(item.get("speaker") or ""),
                    delivery=str(item.get("delivery") or ""),
                    language=str(item.get("language") or ""),
                )
            else:
                _add_dialogue(str(item).strip())
            if len(seen_d) > before:
                llm_hit = True

    # 确定性屏上字始终保留
    for text in base_onscreen:
        _add_onscreen(text)

    llm_touched_quotes = used_labels or bool(payload.get("dialogue")) or bool(
        payload.get("onscreen_text")
    )
    # 未标注，或标注全是无效改写：回退说话线索确定性对白
    if (not llm_touched_quotes) or (llm_touched_quotes and not llm_hit):
        for d in base_dialogue:
            if d.text not in seen_o:
                _add_dialogue(
                    d.text,
                    speaker=d.speaker,
                    delivery=d.delivery,
                    language=d.language,
                )

    # onscreen 优先：对白里若撞车则剔除
    onscreen_set = set(onscreen)
    dialogue = [d for d in dialogue if d.text not in onscreen_set]
    return dialogue, onscreen


def detect_ambiguities(intent: str, shot: ShotConstraint) -> list[str]:
    """只记录不猜测：检测自相矛盾表述。"""
    text = intent or ""
    notes: list[str] = []
    multi_cut = bool(
        re.search(
            r"三个不同场景|多场景|场景切换|切到|跳切|多个?机位|快速切换|四个机位",
            text,
        )
    )
    if shot.single_shot and multi_cut:
        notes.append("单镜头/一镜到底/禁止切镜 与 多场景或跳切 冲突")
    if re.search(r"不要配乐", text) and re.search(r"(?:要|加|带)配乐", text):
        notes.append("不要配乐 与 要配乐 冲突")
    return notes


def extract_visual_locks(intent: str) -> list[str]:
    """抽取必须保留的镜头/成像属性（浅景深、电影感、近景虚影等），并入 must_elements。"""
    text = intent or ""
    hits: list[str] = []
    for m in re.finditer(
        r"浅景深|深景深|大光圈虚化|电影感|胶片颗粒|"
        r"近景行人虚影掠过镜头|行人虚影掠过镜头|虚影掠过镜头|"
        r"shallow\s+depth(?:\s+of\s+field)?|bokeh|"
        r"filmic|cinematic(?:\s+look)?|live[- ]?action",
        text,
        re.I,
    ):
        hits.append(m.group(0).strip())
    return _unique_keep_order(hits)


def extract_must_elements_heuristic(intent: str) -> list[str]:
    """极简启发式主体抽取：仅用于无 LLM 时的兜底，宁缺毋滥。

    不做场景补全；优先抓「一只/一个 + 实体」及少数高置信名词；并并入视觉锁。
    """
    text = (intent or "").strip()
    if not text:
        return []
    hits: list[str] = []
    for m in re.finditer(
        r"(?:一只|一个|一位|一名|一条|一辆|一架)([\u4e00-\u9fffA-Za-z]{1,12})",
        text,
    ):
        hits.append(m.group(0).strip())
    for m in re.finditer(
        r"(橘猫|咖啡|红巴士|球员|产品|笔记本|帆布包|连帽卫衣)",
        text,
    ):
        hits.append(m.group(1))
    hits.extend(extract_visual_locks(text))
    stop = {"禁止", "不要", "不得", "单镜头", "必须", "同时", "风格", "动作", "约秒"}
    filtered = [h for h in hits if h not in stop and len(h) >= 2]
    return _unique_keep_order(filtered)[:12]


def prefer_single_shot_for_short_clip(
    intent: str,
    shot: ShotConstraint,
    duration_sec: float,
    *,
    apply_short_clip_default: bool = True,
) -> ShotConstraint:
    """短片默认偏好单镜（对齐官方 Context-IR）；显式多镜/分镜/追逐多拍请求除外。

    apply_short_clip_default=False 时（LLM/落盘 contract）：只应用显式单镜/多拍覆盖，
    不因 duration≤8 强行改写 LLM 已给出的 single_shot=False。
    """
    text = intent or ""
    # 用户显式要求单镜时优先
    if re.search(r"禁止切镜|不得切镜|不要切镜|单镜头|一镜到底|不要分镜|不得分镜", text):
        return ShotConstraint(single_shot=True, max_shots=1)
    # 武侠/追逐/显式分步多拍：即使 LLM 标了 single_shot，也不压成 1 镜
    if re.search(
        r"多镜头|分镜|切到|第二镜|镜头\s*2|montage|\bcuts?\b|"
        r"追逐|追击|飞身|屋顶追逐|平滑过渡|经.+再|从首帧.+到尾帧|"
        r"先.+再|再.+最后|然后.+最后|先给|再展示|最后字幕|"
        r"两段式|前半|后半|三段式",
        text,
        re.I,
    ):
        return ShotConstraint(single_shot=False, max_shots=shot.max_shots)
    if shot.single_shot or (shot.max_shots is not None and shot.max_shots == 1):
        return ShotConstraint(single_shot=True, max_shots=1)
    if apply_short_clip_default and float(duration_sec or 0) <= 8.0:
        return ShotConstraint(single_shot=True, max_shots=1)
    return shot


def parse_intent_deterministic(intent: str, mode: str = "t2va") -> IntentContract:
    """纯确定性抽取（无 LLM）：对白/屏上字/时长/否定/镜头/动作链/风格。

    用于单测与离线 gate；must_elements 仅弱启发式。
    """
    raw = (intent or "").strip()
    duration_sec = extract_duration_sec(raw)
    shot = prefer_single_shot_for_short_clip(raw, extract_shot_constraint(raw), duration_sec)
    dialogue = extract_dialogue_lines(raw)
    onscreen = extract_onscreen_extended(raw)
    # 口号若已在 onscreen，从 dialogue 再滤一次
    onscreen_set = set(onscreen)
    dialogue = [d for d in dialogue if d.text not in onscreen_set]
    must = extract_must_elements_heuristic(raw)
    # 有张嘴对白时注入「可懂吐字」软锁（听清问题的提示词侧缓解，非成片硬证）
    if any(d.delivery == "spoken" and d.text for d in dialogue):
        must = _unique_keep_order(must + ["口型与台词同步", "吐字清晰可懂"])
    sub_pol = extract_subtitle_policy(raw)
    forbidden = extract_forbidden(raw)
    if sub_pol == "none" and not any("字幕" in f for f in forbidden):
        forbidden = _unique_keep_order(forbidden + ["不要字幕"])
    return IntentContract(
        must_elements=must,
        forbidden=forbidden,
        dialogue=dialogue,
        onscreen_text=onscreen,
        shot_constraint=shot,
        duration_sec=duration_sec,
        action_chain=extract_action_chain(raw),
        explicit_style=extract_explicit_style(raw),
        explicit_negatives=extract_explicit_negatives(raw),
        intent_raw=raw,
        mode=mode,
        ambiguities=detect_ambiguities(raw, shot),
        subtitle_policy=sub_pol,
    )


def _parse_json_obj(raw: str) -> dict[str, Any]:
    """从模型输出中解析 JSON 对象。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("parse_intent 输出不是 JSON 对象")
    return payload


def _as_str_list(value: Any) -> list[str]:
    """把任意值规范成去空白字符串列表。"""
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    out: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("attr") or "").strip()
        else:
            text = str(item).strip()
        if text:
            out.append(text)
    return _unique_keep_order(out)


def contract_from_llm_payload(
    payload: dict[str, Any],
    *,
    intent: str,
    mode: str,
) -> IntentContract:
    """把 LLM JSON 转为 IntentContract；引号角色以 LLM 分类为准，正文仍须逐字。"""
    base = parse_intent_deterministic(intent, mode=mode)
    must = _as_str_list(payload.get("must_elements"))
    forbidden = _as_str_list(payload.get("forbidden")) or base.forbidden
    action = _as_str_list(payload.get("action_chain")) or base.action_chain
    style = payload.get("explicit_style")
    if isinstance(style, str):
        style = style.strip() or None
    else:
        style = base.explicit_style
    negatives = _as_str_list(payload.get("explicit_negatives")) or base.explicit_negatives
    amb = _as_str_list(payload.get("ambiguities")) or base.ambiguities

    shot_raw = payload.get("shot_constraint") or {}
    if isinstance(shot_raw, dict):
        single = bool(shot_raw.get("single_shot", base.shot_constraint.single_shot))
        max_shots = shot_raw.get("max_shots", base.shot_constraint.max_shots)
        if max_shots is not None:
            try:
                max_shots = int(max_shots)
            except (TypeError, ValueError):
                max_shots = base.shot_constraint.max_shots
        shot = ShotConstraint(single_shot=single, max_shots=max_shots)
    else:
        shot = base.shot_constraint

    dur = payload.get("duration_sec")
    try:
        duration = float(dur) if dur is not None else base.duration_sec
        duration = float(max(4, min(15, duration)))
    except (TypeError, ValueError):
        duration = base.duration_sec

    # LLM 已给出 shot_constraint 时：只做追逐/禁止切镜等显式覆盖，不因短片默认改写
    # LLM 未给出时：沿用 deterministic 的短片单镜默认
    shot = prefer_single_shot_for_short_clip(
        intent or "",
        shot,
        duration,
        apply_short_clip_default=not isinstance(shot_raw, dict),
    )

    # 引号：LLM 判断是否台词/屏上字；改写正文一律丢弃，只认意图里的引号原文
    dialogue, onscreen = merge_llm_quote_roles(
        payload,
        intent=intent or "",
        base_onscreen=base.onscreen_text,
        base_dialogue=base.dialogue,
    )
    sub_pol = str(payload.get("subtitle_policy") or "").strip().lower()
    if sub_pol not in {"none", "whitelist", "unspecified"}:
        sub_pol = base.subtitle_policy
    # 显式「不要字幕」优先；否则有屏上白名单时抬到 whitelist
    if base.subtitle_policy == "none":
        sub_pol = "none"
    elif onscreen and sub_pol == "unspecified":
        sub_pol = "whitelist"

    must = _unique_keep_order((must or base.must_elements) + extract_visual_locks(intent or ""))
    if any(d.delivery == "spoken" and d.text for d in dialogue):
        must = _unique_keep_order(must + ["口型与台词同步", "吐字清晰可懂"])
    forbidden = _unique_keep_order(forbidden)
    if sub_pol == "none" and not any("字幕" in f for f in forbidden):
        forbidden = _unique_keep_order(forbidden + ["不要字幕"])
    return IntentContract(
        must_elements=must or base.must_elements,
        forbidden=forbidden,
        dialogue=dialogue,
        onscreen_text=onscreen,
        shot_constraint=shot,
        duration_sec=duration,
        action_chain=action,
        explicit_style=style,
        explicit_negatives=negatives,
        intent_raw=(intent or "").strip(),
        mode=mode,
        ambiguities=_unique_keep_order(amb),
        subtitle_policy=sub_pol,
    )


def parse_intent(
    intent: str,
    mode: str = "t2va",
    *,
    chat: ChatFn | None = None,
    use_llm: bool = True,
) -> IntentContract:
    """解析短意图为 IntentContract。

    Args:
        intent: 用户短意图原文
        mode: t2va/i2va/fl2va/l2va/r2va
        chat: LLM 调用函数，签名同 gemini.chat
        use_llm: False 时仅确定性抽取（单测/离线）

    温度必须由 decode.parse_intent=0.0 保证；本函数在有 chat 时传 stage=parse_intent。
    """
    raw = (intent or "").strip()
    if not use_llm or chat is None:
        return parse_intent_deterministic(raw, mode=mode)

    system = load_prompt("parse_intent")
    quotes = extract_quote_spans(raw)
    quote_block = ""
    if quotes:
        listed = "\n".join(f'{i}. 「{text}」' for i, (_, text) in enumerate(quotes, 1))
        quote_block = (
            "\n\nQuoted spans found in the intent (classify EVERY item in quote_labels; "
            "text must stay character-exact):\n"
            f"{listed}\n"
        )
    user = (
        f"Mode: {mode}\n"
        "Extract Intent Contract JSON only. Do not invent elements absent from the intent.\n"
        "For each quoted span: decide spoken / voiceover / internal / onscreen / ignore.\n"
        f"{quote_block}\n"
        f"Short intent:\n{raw}"
    )
    response = chat(system, user, stage="parse_intent")
    try:
        payload = _parse_json_obj(response)
    except (json.JSONDecodeError, ValueError):
        # LLM 失败时回退确定性，不阻塞管线
        return parse_intent_deterministic(raw, mode=mode)
    return contract_from_llm_payload(payload, intent=raw, mode=mode)


def assert_verbatim_locks(contract: IntentContract, intent: str) -> None:
    """断言对白/屏上文字均是意图子串（逐字）；供单测与回归。"""
    text = intent or ""
    for d in contract.dialogue:
        if d.text not in text:
            raise AssertionError(f"对白被改写或不在原文中: {d.text!r}")
    for line in contract.onscreen_text:
        if line not in text:
            raise AssertionError(f"屏上文字被改写或不在原文中: {line!r}")
