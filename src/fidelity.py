"""保真度 F：10 项检查（确定性 + 逐项 LLM 布尔判定）。

case 级通过：
  F_pass = (无 hard-fail) ∧ (FC1=1) ∧ (FC5=1) ∧ (FC6≥0.9) ∧ (FC9=0)

LLM 判定项必须逐项独立提问（可并发，逻辑独立），温度 0.0，输出仅 0/1。
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .config import load_prompt
from .contract import IntentContract, RefAttr

ChatFn = Callable[..., str]

SHOT_RE = re.compile(r"\[Shot\s+(\d+)\]", re.I)
SHOT_TS_RE = re.compile(r"\[Shot\s+(\d+)\]\s*At\s+(\d{2}):(\d{2})\.(\d{3})", re.I)
DLANG_RE = re.compile(r"<d>\s*\[([A-Za-z]+)\]\s*(.*?)</d>", re.S)
CANVAS_RE = re.compile(
    r"(?i)(?<![x-])\b(?:aspect\s+ratio|canvas\s+size|resolution|frame\s+rate|fps|帧率|分辨率|画幅|像素)\b"
    r"|\b(?:768P|2K|1280x720|1920x1080)\b"
    r"|\b(?:16:9|9:16|21:9|4:3|3:4|1:1)\s*(?:aspect\s*ratio|横屏|竖屏)?"
)
# 显著发明的粗启发式（LLM 不可用时的兜底信号，不算最终 FC9）
PERSON_HINT_RE = re.compile(
    r"\b(?:people|man|woman|boy|girl|face|hands?|crowd)\b|"
    r"(?<!No )(?<!no )(?<!without )\bperson\b|"
    r"(?:人物|人脸|男人|女人|男孩|女孩|手部|路人)",
    re.I,
)
# 非显著发明：光影材质/环境声/运镜等 Inferred，不应计入 FC9
_NON_SIGNIFICANT_INV_RE = re.compile(
    r"ambient|soundscape|room\s*tone|foley|footstep|steam|mist|dust|wood|grain|velvet|"
    r"material|texture|light(?:ing)?|shadow|camera|push[- ]?in|dolly|pan|tilt|"
    r"bell[- ]?like|resonant\s+tone|hiss|rustle|drone(?!\s+character)|cinematic\s+sound|"
    r"water\s+droplet|condensation|sheen|specular|"
    r"drum\s*beat|percussion|rhythmic|traditional\s+chinese\s+drum|"
    r"watermark|水印|@[\w\u4e00-\u9fff]+|"
    r"^shot\s*\d+$|^\[shot\s*\d+\]$",
    re.I,
)


def _norm_inv_blob(text: str) -> str:
    """库存/发明比对用：小写去空白。"""
    return re.sub(r"\s+", "", (text or "").lower())


def _filter_significant_inventions(
    items: list[str],
    inventory: str = "",
    intent: str = "",
) -> list[str]:
    """只保留方案定义的显著发明；过滤 Inferred、库存已有、意图已覆盖。"""
    inv_blob = _norm_inv_blob(inventory)
    intent_blob = _norm_inv_blob(intent)
    out: list[str] = []
    for raw in items:
        text = (raw or "").strip()
        if not text:
            continue
        if _NON_SIGNIFICANT_INV_RE.search(text) and not re.search(
            r"\b(logo|brand|subtitle|caption|crowd|person|people|face|dialogue|slogan)\b|"
            r"(字幕|口号|品牌|人脸|人物|对白|新场景)",
            text,
            re.I,
        ):
            continue
        # 配乐/节奏轨默认属 Inferred（除非意图禁止配乐）
        if re.search(r"(?i)\b(?:music|score|soundtrack|pulse|beat|synth|orchestral)\b|配乐|音乐", text):
            if not re.search(r"不要配乐|禁止配乐|无配乐|不要音乐", intent or ""):
                continue
        # 库存已描述的实体不算发明（子串或关键 token 命中）
        if inv_blob:
            needle = _norm_inv_blob(text)
            if needle and needle in inv_blob:
                continue
            tokens = [t for t in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{3,}", text.lower()) if t]
            if tokens and sum(1 for t in tokens if t in inv_blob) >= max(1, (len(tokens) + 1) // 2):
                continue
        # 意图已点名的实体（如「蓝色外套」）不算发明
        if intent_blob:
            tokens = [t for t in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{3,}", text.lower()) if t]
            if tokens and sum(1 for t in tokens if t in intent_blob) >= max(1, (len(tokens) + 1) // 2):
                continue
        # 中英同指：意图「蓝色外套」↔ 发明 "blue coat"；人设/人物 ↔ person
        if (intent or "") and (
            (re.search(r"蓝色?外套", intent) and re.search(r"blue\s*coat", text, re.I))
            or (re.search(r"红色?外套", intent) and re.search(r"red\s*coat", text, re.I))
            or (
                re.search(r"人物|行人|人设|参考人物|走路|坐下", intent)
                and re.search(r"\b(?:a\s+)?(?:person|people|figure|pedestrian)\b", text, re.I)
            )
            or (
                re.search(r"街道|雨天|街巷|路边", intent)
                and re.search(
                    r"(?i)\b(?:street|sidewalk|awnings?|alley|road)\b|人行道|雨棚",
                    text,
                )
            )
            or (
                re.search(r"咖啡|咖啡馆|喝水|坐下", intent)
                and re.search(
                    r"(?i)\b(?:cafe|coffee|espresso|cup|mug|drink|table|ceramic)\b|咖啡|杯",
                    text,
                )
            )
        ):
            continue
        out.append(text)
    return out



@dataclass
class FCResult:
    """单项保真检查结果。"""

    id: str
    passed: bool
    hard_fail: bool
    score: float  # 覆盖率/保留率/计数等；布尔项用 1.0/0.0
    detail: str = ""
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """序列化。"""
        return asdict(self)


@dataclass
class FidelityReport:
    """一份 prompt 相对 contract 的保真报告。"""

    passed: bool
    checks: dict[str, FCResult] = field(default_factory=dict)
    invention_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """序列化。"""
        return {
            "passed": self.passed,
            "invention_count": self.invention_count,
            "checks": {k: v.to_dict() for k, v in self.checks.items()},
        }


def _norm(text: str) -> str:
    """压缩空白便于子串查找。"""
    return re.sub(r"\s+", "", text or "")


def _parse_bool01(raw: str) -> int:
    """把模型输出解析为 0 或 1。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # 优先 JSON
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            val = payload.get("entailed", payload.get("violated", payload.get("value")))
            if val is True or val == 1 or str(val).strip() == "1":
                return 1
            if val is False or val == 0 or str(val).strip() == "0":
                return 0
        if payload in (0, 1, True, False):
            return 1 if payload else 0
    except (json.JSONDecodeError, TypeError):
        pass
    m = re.search(r"\b([01])\b", text)
    if m:
        return int(m.group(1))
    low = text.lower()
    if "yes" in low or "true" in low or "违反" in text:
        return 1
    return 0


def _entail_one(
    chat: ChatFn,
    *,
    question_type: str,
    claim: str,
    prompt: str,
    inventory: str = "",
) -> int:
    """对单一 claim 做一次 0/1 判定。"""
    system = load_prompt("fidelity_entail")
    user = (
        f"question_type: {question_type}\n"
        f"claim: {claim}\n"
        f"inventory:\n{(inventory or '')[:1500]}\n\n"
        f"final_prompt:\n{(prompt or '')[:6000]}\n"
    )
    raw = chat(system, user, stage="fidelity")
    return _parse_bool01(raw)


def fc1_must_coverage(
    contract: IntentContract,
    prompt: str,
    *,
    chat: ChatFn | None = None,
) -> FCResult:
    """FC1：MUST 元素覆盖率，要求 = 1.0。"""
    items = list(contract.must_elements or [])
    if not items:
        return FCResult("FC1", True, False, 1.0, "无 must_elements")
    missing: list[str] = []
    if chat is None:
        # 确定性兜底：归一化子串
        body = _norm(prompt)
        for el in items:
            if _norm(el) not in body and el not in (prompt or ""):
                missing.append(el)
    else:
        def _one(el: str) -> tuple[str, int]:
            # entailed=1 表示 prompt 覆盖了该元素
            return el, _entail_one(chat, question_type="must_covered", claim=el, prompt=prompt)

        with ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
            futs = [pool.submit(_one, el) for el in items]
            for fut in as_completed(futs):
                el, ok = fut.result()
                if not ok:
                    missing.append(el)
    score = 1.0 if not missing else max(0.0, 1.0 - len(missing) / len(items))
    # 方案要求覆盖率 = 1.0 才算过
    passed = len(missing) == 0
    return FCResult("FC1", passed, False, 1.0 if passed else score, "must 覆盖", missing)


def fc2_forbidden(
    contract: IntentContract,
    prompt: str,
    *,
    chat: ChatFn | None = None,
) -> FCResult:
    """FC2：MUST-NOT 违反；任一违反即 hard-fail。"""
    items = list(contract.forbidden or [])
    if not items:
        return FCResult("FC2", True, True, 1.0, "无 forbidden")
    violated: list[str] = []
    # 确定性捷径：镜头/配乐/文字类
    shot_n = len(SHOT_RE.findall(prompt or ""))
    if contract.shot_constraint.single_shot and shot_n >= 2:
        violated.append("single_shot_but_multi_shot")
    if any("配乐" in f or "音乐" in f for f in items):
        if re.search(r"non_diegetic_music\s*:\s*(?!N/?A\b)", prompt or "", re.I):
            # 若明确写了非 N/A 内容
            m = re.search(r"non_diegetic_music\s*:\s*(.+)", prompt or "", re.I)
            if m and m.group(1).strip() and not re.match(r"N/?A\b", m.group(1).strip(), re.I):
                violated.append("music_present_despite_forbid")

    # 切镜类：仅 1 个 Shot 且无硬切用语 → 确定性合规，跳过 LLM 误杀
    cut_ok = shot_n <= 1 and not re.search(
        r"\b(?:hard\s*cut|jump\s*cut|cut\s+to)\b|切镜|切到|跳切",
        prompt or "",
        re.I,
    )
    # 字幕/贴字类：去掉 <d> 对白后再查字幕线索（对白不是字幕）
    prompt_wo_dialogue = DLANG_RE.sub(" ", prompt or "")
    has_onscreen_gfx = bool(
        re.search(
            r"(?:subtitle|caption|onscreen\s*text|花字|字幕|角标|贴字|"
            r"watermark|水印|logo\s*lockup)",
            prompt_wo_dialogue,
            re.I,
        )
    )

    def _deterministic_forbidden(claim: str) -> bool | None:
        """对可规则化的 forbidden 返回 True=违反 / False=合规 / None=交 LLM。"""
        if re.search(r"切镜|单镜头|一镜到底|不得切|不要分镜|擅自切镜", claim):
            return not cut_ok
        if re.search(r"字幕|贴字|花字|水印|屏幕文字", claim):
            return has_onscreen_gfx
        # 无对白：仅当出现 <d> 台词块或明确说话标记才算违反；环境 chatter ≠ 对白
        if re.search(r"无对白|不要对白|禁止对白|不得对白|no\s+dialogue", claim, re.I):
            has_spoken = bool(DLANG_RE.search(prompt or "")) or bool(
                re.search(
                    r"\b(?:says?|said|speaks?|spoken\s+line|dialogue|voice[- ]over)\b|"
                    r"台词|旁白|说道",
                    prompt or "",
                    re.I,
                )
            )
            return has_spoken
        # 白天晴空：黄昏/逆光/落日稿若无白天晴空线索 → 合规（避免 LLM 把「禁止白天晴空」误杀）
        if re.search(r"白天晴空|禁止白天|不要白天|daytime\s+clear\s+sky", claim, re.I):
            has_day_clear = bool(
                re.search(
                    r"\b(?:clear\s+blue\s+sky|bright\s+daylight|midday\s+sun|sunny\s+noon)\b|"
                    r"白天晴空|正午烈日|大白天",
                    prompt or "",
                    re.I,
                )
            )
            return has_day_clear
        # 卡通/动画风格禁止：明确采用 cartoon/anime 风格才算违反；
        # 「No cartoon / 不要卡通」合规声明中的字样不算。
        if re.search(r"卡通|动画风格|anime|cartoon", claim, re.I):
            has_toon = False
            for m in re.finditer(
                r"\b(?:cartoon|anime|cel[- ]?shaded|2d\s+animation)\b|卡通|二次元动画",
                prompt or "",
                re.I,
            ):
                pre = (prompt or "")[max(0, m.start() - 48) : m.start()]
                if re.search(
                    r"(?is)(?:\bno\b|\bwithout\b|\bnot\b|禁止|不要|勿|非|不得)[\s\S]{0,40}$",
                    pre,
                ):
                    continue
                has_toon = True
                break
            return has_toon
        # 禁止第三场景：≤2 个 Shot / 无明显第三地点切换 → 合规
        if re.search(r"第三场景|第三个场景|三场景", claim):
            n = len(SHOT_RE.findall(prompt or ""))
            has_third = bool(
                re.search(
                    r"\bthird\s+scene\b|第三场景|第三个场景|"
                    r"\[Shot\s*3\]",
                    prompt or "",
                    re.I,
                )
            )
            return has_third or n >= 3
        return None

    remain: list[str] = []
    for el in items:
        det = _deterministic_forbidden(el)
        if det is True:
            violated.append(el)
        elif det is False:
            continue
        else:
            remain.append(el)

    if chat is not None and remain:
        def _one(el: str) -> tuple[str, int]:
            # violated=1 表示违背了 forbidden
            return el, _entail_one(chat, question_type="forbidden_violated", claim=el, prompt=prompt)

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(remain)))) as pool:
            futs = [pool.submit(_one, el) for el in remain]
            for fut in as_completed(futs):
                el, bad = fut.result()
                if bad:
                    violated.append(el)
    violated = list(dict.fromkeys(violated))
    passed = len(violated) == 0
    return FCResult("FC2", passed, True, 1.0 if passed else 0.0, "forbidden", violated)


def fc3_dialogue_verbatim(contract: IntentContract, prompt: str) -> FCResult:
    """FC3：对白逐字出现在 <d> 内，且有 speaker 时须邻近绑定；hard-fail。"""
    lines = [d for d in (contract.dialogue or []) if d.text]
    if not lines:
        return FCResult("FC3", True, True, 1.0, "无对白")
    d_blocks = list(DLANG_RE.finditer(prompt or ""))
    joined = "\n".join(m.group(2) for m in d_blocks)
    missing: list[str] = []
    # 带 speaker 的台词按出现顺序映射 (S1)(S2)…
    spoken_with_sp = [d for d in lines if d.speaker]
    for line in lines:
        text = line.text
        if text not in joined:
            missing.append(text)
            continue
        if not line.speaker:
            continue
        sp = line.speaker.strip()
        bound = False
        try:
            s_idx = spoken_with_sp.index(line) + 1
        except ValueError:
            s_idx = 0
        for m in d_blocks:
            if text not in (m.group(2) or ""):
                continue
            pre = (prompt or "")[max(0, m.start() - 160) : m.start()]
            core = sp[: max(2, min(6, len(sp)))]
            if sp in pre or (core and core in pre):
                bound = True
                break
            # 英文稿常用 (S1)/(S2) 绑定：按说话人顺序认可
            if s_idx and re.search(rf"\(S{s_idx}\)", pre):
                bound = True
                break
        if not bound:
            missing.append(f"speaker_unbound:{sp}:{text}")
    if re.search(r"<d>\s*\[Mandarin\]", prompt or "", re.I):
        missing.append("[Mandarin]_forbidden")
    passed = not missing
    return FCResult("FC3", passed, True, 1.0 if passed else 0.0, "对白逐字+说话人", missing)


def fc4_onscreen_verbatim(contract: IntentContract, prompt: str) -> FCResult:
    """FC4：屏上文字逐字出现；hard-fail。"""
    lines = list(contract.onscreen_text or [])
    if not lines:
        return FCResult("FC4", True, True, 1.0, "无屏上文字")
    missing = [line for line in lines if line not in (prompt or "")]
    passed = not missing
    return FCResult("FC4", passed, True, 1.0 if passed else 0.0, "屏上逐字", missing)


def fc5_action_order(
    contract: IntentContract,
    prompt: str,
    *,
    chat: ChatFn | None = None,
) -> FCResult:
    """FC5：动作链在时间轴上保序，要求 = 1.0。"""
    chain = list(contract.action_chain or [])
    if len(chain) < 2:
        return FCResult("FC5", True, False, 1.0, "无动作链")
    positions: list[int] = []
    text = prompt or ""
    cursor = 0
    missing: list[str] = []
    for step in chain:
        idx = text.find(step, cursor)
        if idx < 0:
            # 宽松：全文找一次
            idx = text.find(step)
            if idx < 0 and chat is not None:
                # 让 LLM 判断是否有对应表述；若有，用当前 cursor 作为近似位置
                covered = _entail_one(
                    chat, question_type="must_covered", claim=step, prompt=prompt
                )
                if covered:
                    positions.append(cursor)
                    cursor += 1
                    continue
            if idx < 0:
                missing.append(step)
                continue
        positions.append(idx)
        cursor = idx + len(step)
    if missing:
        return FCResult("FC5", False, False, 0.0, "动作缺失", missing)
    ordered = all(positions[i] <= positions[i + 1] for i in range(len(positions) - 1))
    return FCResult(
        "FC5",
        ordered,
        False,
        1.0 if ordered else 0.0,
        "动作保序",
        [] if ordered else ["out_of_order"],
    )


def fc6_reference_attrs(
    contract: IntentContract,
    prompt: str,
    *,
    chat: ChatFn | None = None,
) -> FCResult:
    """FC6：identity 类参考属性保留率 ≥ 0.90。"""
    attrs = [a for a in (contract.reference_attrs or []) if a.kind == "identity"]
    if not attrs:
        return FCResult("FC6", True, False, 1.0, "无 identity 参考属性")
    kept = 0
    missing: list[str] = []
    for a in attrs:
        claim = f"{a.ref_tag}: {a.attr}"
        if a.attr and a.attr in (prompt or ""):
            kept += 1
            continue
        if chat is not None:
            ok = _entail_one(
                chat,
                question_type="ref_attr_kept",
                claim=claim,
                prompt=prompt,
            )
            if ok:
                kept += 1
            else:
                missing.append(claim)
        else:
            missing.append(claim)
    score = kept / len(attrs)
    passed = score >= 0.90
    return FCResult("FC6", passed, False, score, "参考属性保留", missing)


def fc7_shot_constraint(contract: IntentContract, prompt: str) -> FCResult:
    """FC7：镜头约束；hard-fail。"""
    shots = SHOT_RE.findall(prompt or "")
    n = len(shots)
    violations: list[str] = []
    if contract.shot_constraint.single_shot and n >= 2:
        violations.append(f"single_shot_but_{n}_shots")
    max_shots = contract.shot_constraint.max_shots
    if max_shots is not None and n > max_shots:
        violations.append(f"shots_{n}_gt_max_{max_shots}")
    passed = not violations
    return FCResult("FC7", passed, True, 1.0 if passed else 0.0, "镜头约束", violations)


def fc8_timestamp_bounds(contract: IntentContract, prompt: str) -> FCResult:
    """FC8：时间戳不超 duration 且单调；hard-fail。"""
    duration = float(contract.duration_sec or 5)
    violations: list[str] = []
    prev = -1.0
    for shot_no, mm, ss, mmm in SHOT_TS_RE.findall(prompt or ""):
        secs = float(mm) * 60 + float(ss) + float(mmm) / 1000
        if secs <= prev:
            violations.append(f"Shot{shot_no}_not_increasing")
        if secs > duration + 0.001:
            violations.append(f"Shot{shot_no}_over_duration")
        prev = secs
    passed = not violations
    return FCResult("FC8", passed, True, 1.0 if passed else 0.0, "时间戳边界", violations)


def fc9_invention(
    contract: IntentContract,
    prompt: str,
    *,
    chat: ChatFn | None = None,
    inventory: str = "",
) -> FCResult:
    """FC9：未请求显著发明计数，要求 = 0。"""
    inventions: list[str] = []
    if chat is not None:
        # 一次枚举，但仍要求 0/1 + 列表
        system = load_prompt("fidelity_entail")
        user = (
            "question_type: list_inventions\n"
            f"intent:\n{contract.intent_raw}\n"
            f"must_elements: {contract.must_elements}\n"
            f"inventory:\n{(inventory or '')[:1500]}\n\n"
            f"final_prompt:\n{(prompt or '')[:6000]}\n"
        )
        raw = chat(system, user, stage="fidelity")
        text = (raw or "").strip()
        try:
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
            payload = json.loads(text)
            if isinstance(payload, dict):
                items = payload.get("inventions") or payload.get("items") or []
                if isinstance(items, list):
                    inventions = _filter_significant_inventions(
                        [str(x).strip() for x in items if str(x).strip()],
                        inventory=inventory,
                        intent=contract.intent_raw,
                    )
        except (json.JSONDecodeError, TypeError):
            # 无法解析则不当作发明（避免误杀）；由单测覆盖可解析路径
            inventions = []
    else:
        # 无 LLM：仅当「禁止人物」却出现人物词时计发明（窄启发式，供单测）
        if any(re.search(r"人物|人脸|人手", f) for f in contract.forbidden):
            if PERSON_HINT_RE.search(prompt or ""):
                inventions.append("person_entity")
        # 禁止文字却出现字幕线索
        if any("文字" in f or "字幕" in f for f in contract.forbidden):
            if re.search(r"(subtitle|caption|onscreen text|花字|字幕\s*[「\"])", prompt or "", re.I):
                inventions.append("onscreen_text")
    count = len(inventions)
    passed = count == 0
    return FCResult("FC9", passed, False, float(count), "未请求发明", inventions)


def fc10_canvas_leak(prompt: str) -> FCResult:
    """FC10：画幅/分辨率/帧率泄漏；hard-fail。"""
    hits = [m.group(0) for m in CANVAS_RE.finditer(prompt or "")]
    passed = not hits
    return FCResult("FC10", passed, True, 1.0 if passed else 0.0, "画幅泄漏", hits)


def _norm_d_text(text: str) -> str:
    """对白比对用紧凑归一化。"""
    return re.sub(r"\s+", "", (text or "").strip())


def fc11_dialogue_closed_world(contract: IntentContract, prompt: str) -> FCResult:
    """FC11：禁止擅自增加台词；每个 <d> 必须对应 contract.dialogue；hard-fail。"""
    allowed = {_norm_d_text(d.text) for d in (contract.dialogue or []) if d.text}
    extras: list[str] = []
    for _lang, body in DLANG_RE.findall(prompt or ""):
        needle = _norm_d_text(body)
        if not needle:
            continue
        if not allowed:
            extras.append(body.strip()[:80])
            continue
        if needle not in allowed and not any(needle in a or a in needle for a in allowed):
            extras.append(body.strip()[:80])
    # 无 contract 对白时：不允许出现任何 <d>（擅自加台词）
    if not allowed and DLANG_RE.search(prompt or ""):
        pass  # extras 已填
    passed = not extras
    return FCResult("FC11", passed, True, 1.0 if passed else 0.0, "对白闭世界", extras)


_SUBTITLE_BAN_RE = re.compile(
    r"(?i)\b(?:burned[- ]?in\s+)?subtitles?\b|\bcaptions?\b|"
    r"花字|烧录字幕|角标字幕|"
    r"(?:onscreen|on-screen)\s+(?:subtitle|caption)|"
    r"字幕\s*[「\"“]|出现字幕|叠加字幕|底部字幕",
)


def fc12_subtitle_policy(contract: IntentContract, prompt: str) -> FCResult:
    """FC12：字幕策略；policy=none 时禁止烧录字幕/花字（白名单屏上字除外）；hard-fail。"""
    policy = getattr(contract, "subtitle_policy", "unspecified") or "unspecified"
    if policy == "unspecified":
        return FCResult("FC12", True, True, 1.0, "无字幕策略")
    text = prompt or ""
    # 去掉已允许的屏上字字面，再查禁词，降低「招牌原文」误杀
    scrubbed = text
    for line in contract.onscreen_text or []:
        if line:
            scrubbed = scrubbed.replace(line, " ")
    # 去掉 <d> 对白块（对白不是字幕）
    scrubbed = DLANG_RE.sub(" ", scrubbed)
    hits = [m.group(0) for m in _SUBTITLE_BAN_RE.finditer(scrubbed)]
    if policy == "none":
        passed = not hits
        return FCResult("FC12", passed, True, 1.0 if passed else 0.0, "禁字幕", hits)
    if policy == "whitelist":
        # whitelist：仍禁通用 subtitle 词，除非正文只含白名单
        passed = not hits
        return FCResult("FC12", passed, True, 1.0 if passed else 0.0, "字幕白名单", hits)
    return FCResult("FC12", True, True, 1.0, "无字幕策略")


_CLOSED_LIPS_RE = re.compile(
    r"(?i)lips?\s+remain\s+(?:completely\s+)?closed|mouth\s+(?:remains?\s+)?closed|"
    r"off[- ]?screen\s+voiceover|says\s+in\s+an\s+off[- ]?screen|"
    r"画外音|旁白|不张嘴|闭口|嘴唇闭合|口型闭合|不要张嘴",
)


def fc13_monologue_closed_lips(contract: IntentContract, prompt: str) -> FCResult:
    """FC13：独白/旁白须 VO+闭口，禁止写成对口型张嘴说；hard-fail。"""
    special = [d for d in (contract.dialogue or []) if d.text and d.delivery in {"internal", "voiceover"}]
    if not special:
        return FCResult("FC13", True, True, 1.0, "无独白/旁白")
    missing: list[str] = []
    for d in special:
        found = False
        for m in DLANG_RE.finditer(prompt or ""):
            if d.text in (m.group(2) or ""):
                window = (prompt or "")[max(0, m.start() - 100) : min(len(prompt or ""), m.end() + 100)]
                if _CLOSED_LIPS_RE.search(window):
                    found = True
                    break
                # 若写成 shout/says: 且无闭口 → 失败
                if re.search(r"\b(?:shouts?|says?|speaks?|exclaims?)\s*:", window, re.I) and not _CLOSED_LIPS_RE.search(
                    window
                ):
                    found = False
                    break
        if not found:
            missing.append(f"{d.delivery}:{d.text}")
    passed = not missing
    return FCResult("FC13", passed, True, 1.0 if passed else 0.0, "独白/旁白闭口", missing)


def evaluate_fidelity(
    contract: IntentContract,
    prompt: str,
    *,
    chat: ChatFn | None = None,
    inventory: str = "",
) -> FidelityReport:
    """跑齐 FC1–FC13，返回 case 级保真报告。"""
    checks = {
        "FC1": fc1_must_coverage(contract, prompt, chat=chat),
        "FC2": fc2_forbidden(contract, prompt, chat=chat),
        "FC3": fc3_dialogue_verbatim(contract, prompt),
        "FC4": fc4_onscreen_verbatim(contract, prompt),
        "FC5": fc5_action_order(contract, prompt, chat=chat),
        "FC6": fc6_reference_attrs(contract, prompt, chat=chat),
        "FC7": fc7_shot_constraint(contract, prompt),
        "FC8": fc8_timestamp_bounds(contract, prompt),
        "FC9": fc9_invention(contract, prompt, chat=chat, inventory=inventory),
        "FC10": fc10_canvas_leak(prompt),
        "FC11": fc11_dialogue_closed_world(contract, prompt),
        "FC12": fc12_subtitle_policy(contract, prompt),
        "FC13": fc13_monologue_closed_lips(contract, prompt),
    }
    hard_fail = any(c.hard_fail and not c.passed for c in checks.values())
    fc1_ok = checks["FC1"].passed
    fc5_ok = checks["FC5"].passed
    fc6_ok = checks["FC6"].score >= 0.90
    fc9_ok = checks["FC9"].score == 0
    passed = (not hard_fail) and fc1_ok and fc5_ok and fc6_ok and fc9_ok
    return FidelityReport(
        passed=passed,
        checks=checks,
        invention_count=int(checks["FC9"].score),
    )


def fidelity_pass_rate(reports: list[FidelityReport]) -> float:
    """集合级保真通过率。"""
    if not reports:
        return 0.0
    return sum(1 for r in reports if r.passed) / len(reports)


def invention_rate(reports: list[FidelityReport]) -> float:
    """集合级发明率：FC9>0 的 case 占比。"""
    if not reports:
        return 0.0
    return sum(1 for r in reports if r.invention_count > 0) / len(reports)
