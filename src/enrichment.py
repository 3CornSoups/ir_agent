"""丰富度 E：6 项指标，归一到 0–1，仅在 F 通过的 case 上统计。

E = mean(EN1..EN6)；无 gold_ir 时 EN5 用其余 5 项均值填充。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any, Callable

from .contract import IntentContract

ChatFn = Callable[..., str]

WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?|[\u4e00-\u9fff]{1,3}")
SHOT_TS_RE = re.compile(r"\[Shot\s+\d+\]\s*At\s+(\d{2}):(\d{2})\.(\d{3})", re.I)
# 单镜头连续动作里的节拍时间（无新 Shot 头）
BARE_TS_RE = re.compile(r"(?<!\d)At\s+(\d{2}):(\d{2})\.(\d{3})", re.I)
SHOT_RE = re.compile(r"\[Shot\s+\d+\]", re.I)
# 可执行视觉名词/材质/光影词表：覆盖常见英文变体与中文锚点
VISUAL_NOUN_RE = re.compile(
    r"\b(?:lights?|lighting|sunlight|streetlights?|headlights?|taillights?|"
    r"shadows?|grain|fabric|metallic|metals?|glasses?|skin|hairs?|"
    r"dust|rains?|fogs?|mists?|smokes?|steams?|"
    r"reflections?|reflective|reflecting|reflects?|specular|bokeh|textures?|"
    r"wooden|hardwood|woods?|stones?|leather|silks?|velvet|chrome|"
    r"neon|brick|pavement|concrete|glow|glows|glowing|"
    r"highlights?|wetness|sheen|glint|haze|particle|motes?)\b|"
    r"(?:光影|阴影|材质|反光|纹理|尘埃|雨丝|雾气|皮肤|发丝|木纹|金属|玻璃|布料|"
    r"霓虹|砖墙|路面|蒸汽|湿润|高光|柔光)",
    re.I,
)
# 镜头运动：避免把「tilts its head / static state」等主体动作误计为运镜
CAMERA_MOVE_RE = re.compile(
    r"\b(?:dolly|dollies|truck|crane|handheld|"
    r"push(?:es)?[- ]?in|pull(?:s)?[- ]?out|orbit(?:s)?|tracks?\b|"
    r"locked[- ]?off)\b|"
    r"\bcamera\s+(?:pans?|tilts?|holds?|tracks?)\b|"
    r"\b(?:pans?|tilts?)\s+(?:slowly|gently|laterally|left|right|up|down)\b|"
    r"\bstatic\s+(?:\w+\s+){0,3}(?:hold|shot|frame)\b|"
    r"\b(?:slow|gentle|steady)\s+(?:push[- ]?in|pull[- ]?out|dolly|pan|tilt|hold)\b|"
    r"(?:缓推|急推|横摇|俯仰|升降|手持|环绕|跟拍|推近|拉远|固定机位|静持)",
    re.I,
)
AMP_RE = re.compile(
    r"\b(?:slow|fast|gentle|subtle|dramatic|slight|rapid|steady|"
    r"amplitude|small|large|wide|tight|lateral|laterally|medium|close)\b|"
    r"缓|慢|快|轻|大幅度|微|小幅|大幅",
    re.I,
)
CUT_ONLY_RE = re.compile(r"camera\s+cuts?\s+to|the\s+shot\s+cuts?\s+to|shot\s+transitions?\s+to", re.I)
SOUND_ENV_RE = re.compile(
    r"ambience|ambient|room tone|environment|atmosphere|"
    r"\b(?:rain|wind|thunder|birds?|traffic|crowd|hum|rumble|hiss|"
    r"pitter[- ]?patter|drizzle|waves?|surf|crickets?)\b|"
    r"环境声|氛围|风声|雨声|街声|鸟鸣|人声嘈杂|底噪",
    re.I,
)
SOUND_FOLEY_RE = re.compile(r"foley|footstep|rustle|动作声|脚步|衣料|碰撞|倒水", re.I)
SOUND_MUSIC_RE = re.compile(r"music|score|配乐|non_diegetic|underscore", re.I)
MUSIC_NA_RE = re.compile(r"non_diegetic_music\s*:\s*N/?A\b", re.I)


@dataclass
class ENResult:
    """单项丰富度结果。"""

    id: str
    score: float
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """序列化。"""
        return asdict(self)


@dataclass
class EnrichmentReport:
    """一份 prompt 的丰富度报告。"""

    score: float
    checks: dict[str, ENResult] = field(default_factory=dict)
    used_gold: bool = False

    def to_dict(self) -> dict[str, Any]:
        """序列化。"""
        return {
            "score": self.score,
            "used_gold": self.used_gold,
            "checks": {k: v.to_dict() for k, v in self.checks.items()},
        }


def _word_count(text: str) -> int:
    """粗粒度词数（英文词 + 中文 1–3 字块）。"""
    return max(1, len(WORD_RE.findall(text or "")))


def _clamp01(x: float) -> float:
    """夹到 [0,1]。"""
    return max(0.0, min(1.0, float(x)))


def en1_visual_density(prompt: str, *, p75_per_100: float = 3.3) -> ENResult:
    """EN1：锚定视觉细节密度（可执行视觉名词短语 / 100 词），按官方稿语料 p75≈3.3 截断映射。"""
    words = _word_count(prompt)
    hits = len(VISUAL_NOUN_RE.findall(prompt or ""))
    per100 = hits / words * 100.0
    score = _clamp01(per100 / p75_per_100)
    return ENResult("EN1", score, f"per100={per100:.2f}")


def en2_temporal_coverage(prompt: str, duration_sec: float) -> ENResult:
    """EN2：时间轴被节拍覆盖的秒数 / duration。"""
    dur = max(1e-6, float(duration_sec or 5))
    stamps = []
    for mm, ss, mmm in SHOT_TS_RE.findall(prompt or ""):
        stamps.append(float(mm) * 60 + float(ss) + float(mmm) / 1000)
    # 补充正文中的 At 00:SS.mmm（单镜头多节拍）
    for mm, ss, mmm in BARE_TS_RE.findall(prompt or ""):
        stamps.append(float(mm) * 60 + float(ss) + float(mmm) / 1000)
    stamps = sorted(set(stamps))
    if not stamps:
        # 无时间戳：若有镜头描述，给部分分
        n_shots = len(SHOT_RE.findall(prompt or ""))
        score = _clamp01(0.4 if n_shots >= 1 else 0.0)
        return ENResult("EN2", score, "no_timestamps")
    # 近似：片头→首拍 + 相邻节拍间隔 + 末拍→片尾（各段上限 2s 合理默认）
    covered = min(2.0, max(0.0, stamps[0]))
    for i in range(len(stamps) - 1):
        covered += min(dur, max(0.0, stamps[i + 1] - stamps[i]))
    covered += min(2.0, max(0.0, dur - stamps[-1]))
    # 若只有一个时间戳，用 duration*0.5 与片头尾覆盖取较大
    if len(stamps) == 1:
        covered = max(covered, min(dur, max(dur * 0.5, 1.0)))
    score = _clamp01(covered / dur)
    return ENResult("EN2", score, f"stamps={len(stamps)}")


def en3_sound_layers(prompt: str, *, music_forbidden: bool = False) -> ENResult:
    """EN3：环境声 / 动作声 / 配乐三层齐全度（含合理 N/A）。"""
    text = prompt or ""
    layers = 0
    if SOUND_ENV_RE.search(text):
        layers += 1
    if SOUND_FOLEY_RE.search(text) or re.search(r"overall_soundscape\s*:\s*\S", text, re.I):
        layers += 1
    if music_forbidden or MUSIC_NA_RE.search(text):
        layers += 1  # 合理 N/A 算齐全
    elif SOUND_MUSIC_RE.search(text):
        layers += 1
    score = layers / 3.0
    return ENResult("EN3", score, f"layers={layers}")


def en4_camera_specificity(prompt: str, *, chat: ChatFn | None = None) -> ENResult:
    """EN4：每个「运镜描述」是否同时有类型 + 幅度 + 速度（纯切镜不计入分母）。"""
    shots = re.split(r"\[Shot\s+\d+\]", prompt or "", flags=re.I)
    bodies = [s for s in shots[1:] if s.strip()] or [prompt or ""]
    ok = 0
    judged = 0
    for body in bodies:
        has_type = bool(CAMERA_MOVE_RE.search(body))
        # 仅有 cut / transition、无真实运镜词 → 不纳入 EN4 分母
        if not has_type:
            if CUT_ONLY_RE.search(body) or not re.search(r"\bcamera\b", body, re.I):
                continue
        judged += 1
        has_amp = bool(AMP_RE.search(body))
        has_speed = bool(
            re.search(r"\b(?:slow|fast|steady|rapid|tempo|speed)\b|缓|急|速度", body, re.I)
        ) or has_amp
        if has_type and has_amp and has_speed:
            ok += 1
        elif chat is not None:
            raw = chat(
                "Reply JSON {\"value\":0|1} only. Does this shot describe move type AND amplitude AND speed?",
                body[:1500],
                stage="enrichment",
            )
            try:
                val = json_value01(raw)
            except Exception:  # noqa: BLE001
                val = 0
            ok += val
    if judged == 0:
        # 全文无运镜：给中性分，避免无相机场景被拖死
        return ENResult("EN4", 0.5, "no_camera_moves")
    score = ok / judged
    return ENResult("EN4", _clamp01(score), f"ok_shots={ok}/{judged}")


def json_value01(raw: str) -> int:
    """解析 {\"value\":0|1}。"""
    import json

    text = (raw or "").strip()
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            v = payload.get("value")
            return 1 if v in (1, True, "1") else 0
    except json.JSONDecodeError:
        m = re.search(r"\b([01])\b", text)
        return int(m.group(1)) if m else 0
    return 0


def en5_density_ratio(local_prompt: str, gold_prompt: str | None) -> ENResult | None:
    """EN5：本地词数 / 官方词数，[0.8,1.2]→1.0，越界线性衰减。无 gold 返回 None。"""
    if not (gold_prompt or "").strip():
        return None
    local_n = _word_count(local_prompt)
    gold_n = _word_count(gold_prompt or "")
    ratio = local_n / max(1, gold_n)
    if 0.8 <= ratio <= 1.2:
        score = 1.0
    elif ratio < 0.8:
        score = _clamp01(ratio / 0.8)
    else:
        # ratio>1.2：1.2→1.0，2.4→0
        score = _clamp01(1.0 - (ratio - 1.2) / 1.2)
    return ENResult("EN5", score, f"ratio={ratio:.3f}")


def en6_inferred_gain(
    intent: str,
    prompt: str,
    *,
    chat: ChatFn | None = None,
) -> ENResult:
    """EN6：相对短意图新增的合理细节数，映射 0–1。"""
    if chat is not None:
        raw = chat(
            'Count reasonable inferred details in prompt not present in intent (not inventions). '
            'JSON {"count": N} only.',
            f"intent:\n{intent}\n\nprompt:\n{prompt[:4000]}",
            stage="enrichment",
        )
        import json

        try:
            payload = json.loads(raw.strip())
            count = int(payload.get("count", 0)) if isinstance(payload, dict) else 0
        except (json.JSONDecodeError, TypeError, ValueError):
            count = 0
    else:
        # 启发式：prompt 视觉词 - intent 视觉词
        p_hits = len(VISUAL_NOUN_RE.findall(prompt or ""))
        i_hits = len(VISUAL_NOUN_RE.findall(intent or ""))
        cam = len(CAMERA_MOVE_RE.findall(prompt or ""))
        count = max(0, p_hits - i_hits) + cam
    # 映射：0→0，8+→1
    score = _clamp01(count / 8.0)
    return ENResult("EN6", score, f"count={count}")


def evaluate_enrichment(
    contract: IntentContract,
    prompt: str,
    *,
    gold_prompt: str | None = None,
    chat: ChatFn | None = None,
    music_forbidden: bool | None = None,
) -> EnrichmentReport:
    """计算 6 项丰富度并取均值。"""
    forbid_music = music_forbidden
    if forbid_music is None:
        forbid_music = any("配乐" in f or "音乐" in f for f in (contract.forbidden or []))

    checks: dict[str, ENResult] = {
        "EN1": en1_visual_density(prompt),
        "EN2": en2_temporal_coverage(prompt, contract.duration_sec),
        "EN3": en3_sound_layers(prompt, music_forbidden=bool(forbid_music)),
        "EN4": en4_camera_specificity(prompt, chat=chat),
        "EN6": en6_inferred_gain(contract.intent_raw, prompt, chat=chat),
    }
    en5 = en5_density_ratio(prompt, gold_prompt)
    used_gold = en5 is not None
    if en5 is not None:
        checks["EN5"] = en5
    else:
        fill = sum(c.score for c in checks.values()) / max(1, len(checks))
        checks["EN5"] = ENResult("EN5", fill, "filled_from_others")

    score = sum(c.score for c in checks.values()) / 6.0
    return EnrichmentReport(score=score, checks=checks, used_gold=used_gold)


def enrichment_median(reports: list[EnrichmentReport]) -> float:
    """集合级丰富度中位数。"""
    if not reports:
        return 0.0
    return float(median(r.score for r in reports))
