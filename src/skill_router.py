"""按短意图动态加载风格 skill（底座 h3-prompt-writing 始终另行注入）。

路由对齐 Anthropic skill 元数据先行、正文按需加载：
1. catalog.yaml 的 description/triggers（cues）给前置模型打分用
2. 只有 top1 达阈值的 overlay 才拼进 expand / format
3. 风格稿不得覆盖官方字段名、对齐句、标签规则

参考：
- MiniMax 官方 8 个题材 skill（Hub 工具已剥离）
- swan7-py/MiniMax-H3-Skills-Local（本地纯提示词）
- sjh00/minimax-h3-storyboard-prompt-skills（压成写法而非成片工程）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Iterable

from .config import ROOT, load_prompt

SKILLS_DIR = ROOT / "skills"
CATALOG_PATH = SKILLS_DIR / "catalog.yaml"
# hybrid / llm 走 LLM 打分；keyword 保留真实关键词命中路由（本地行为）。
ROUTER_MODES = ("off", "keyword", "hybrid", "llm")
# LLM 打分路由默认阈值（catalog.yaml 可配 llm_score_threshold / 兼容旧 match_threshold）
DEFAULT_LLM_THRESHOLD = 0.6
_EN_WORD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+'’.\- ]{1,}$")
ClassifyFn = Callable[..., str]


@dataclass(frozen=True)
class StyleSkill:
    """一条可按需加载的风格写作 skill。"""

    id: str
    title: str
    overlay_path: str
    description: str
    triggers: tuple[str, ...] = ()
    origin: str = "official"  # official | community
    upstream: str = ""


@dataclass
class SkillSelection:
    """一次任务选中的风格 skill 及来源。"""

    ids: list[str] = field(default_factory=list)
    source: str = "none"
    llm_scores: dict[str, float] = field(default_factory=dict)
    llm_threshold: float | None = None
    llm_top1_score: float | None = None
    keyword_scores: dict[str, float] = field(default_factory=dict)

    @property
    def scores(self) -> dict[str, float]:
        """本次路由分数（LLM 打分或关键词得分），供 run.json 记录。"""
        if self.llm_scores:
            return self.llm_scores
        return self.keyword_scores

    @property
    def threshold(self) -> float:
        """本次路由阈值（LLM 打分模式）。"""
        if self.llm_threshold is not None:
            return self.llm_threshold
        return DEFAULT_LLM_THRESHOLD

    def overlay_pairs(self) -> list[tuple[str, str]]:
        """返回 (id, overlay 正文) 供 format SYSTEM 拼接。"""
        catalog = {item.id: item for item in load_catalog()}
        pairs: list[tuple[str, str]] = []
        for sid in self.ids:
            item = catalog.get(sid)
            if item is None:
                continue
            pairs.append((sid, load_overlay(item)))
        return pairs

    def detail_records(self) -> list[dict[str, str]]:
        """供 run.json：每条 skill 的 id / origin / upstream。"""
        catalog = {item.id: item for item in load_catalog()}
        out: list[dict[str, str]] = []
        for sid in self.ids:
            item = catalog.get(sid)
            if item is None:
                out.append({"id": sid, "origin": "unknown", "upstream": ""})
                continue
            out.append(
                {
                    "id": item.id,
                    "origin": item.origin,
                    "upstream": item.upstream,
                }
            )
        return out

    def llm_route_meta(self) -> dict[str, Any] | None:
        """LLM 打分路由摘要；未走 LLM 时返回 None。"""
        if self.llm_threshold is None and not self.llm_scores:
            return None
        accepted = (
            self.llm_top1_score is not None
            and self.llm_threshold is not None
            and self.llm_top1_score >= self.llm_threshold
        )
        return {
            "threshold": self.llm_threshold,
            "top1_score": self.llm_top1_score,
            "accepted": accepted,
            "scores": {k: round(v, 4) for k, v in sorted(self.llm_scores.items())},
        }


def normalize_router_mode(router: str | None) -> str:
    """规范化 skill_router 模式；非法值抛错。"""
    mode = (router or "hybrid").strip().lower()
    if mode not in ROUTER_MODES:
        raise ValueError(f"skill_router 须为 {' / '.join(ROUTER_MODES)}")
    return mode


def _as_str_list(value: Any) -> list[str]:
    """把 YAML 列表规范成去空字符串。"""
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


@lru_cache(maxsize=1)
def load_catalog() -> tuple[StyleSkill, ...]:
    """读取 skills/catalog.yaml；缺文件时返回空目录。"""
    if not CATALOG_PATH.is_file():
        return ()
    data = load_yaml_path(CATALOG_PATH)
    items: list[StyleSkill] = []
    for raw in data.get("skills") or []:
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get("id") or "").strip()
        overlay = str(raw.get("overlay") or "").strip()
        if not sid or not overlay:
            continue
        origin = str(raw.get("origin") or "official").strip().lower() or "official"
        if origin not in {"official", "community"}:
            origin = "official"
        upstream = str(raw.get("upstream") or "").strip()
        items.append(
            StyleSkill(
                id=sid,
                title=str(raw.get("title") or sid).strip(),
                overlay_path=overlay,
                description=" ".join(str(raw.get("description") or "").split()),
                triggers=tuple(_as_str_list(raw.get("triggers"))),
                origin=origin,
                upstream=upstream,
            )
        )
    return tuple(items)


def load_yaml_path(path: Any) -> dict[str, Any]:
    """读取任意 YAML 字典；供 catalog 使用。"""
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置不是字典: {path}")
    return data


def catalog_max_skills() -> int:
    """单次任务最多加载的风格 skill 数（forced 合并上限）。"""
    if not CATALOG_PATH.is_file():
        return 2
    data = load_yaml_path(CATALOG_PATH)
    try:
        n = int(data.get("max_skills") or 2)
    except (TypeError, ValueError):
        n = 2
    return max(1, min(3, n))


def catalog_match_threshold() -> float:
    """读取 skill 语义匹配分阈值；优先 llm_score_threshold，兼容旧 match_threshold。"""
    if not CATALOG_PATH.is_file():
        return DEFAULT_LLM_THRESHOLD
    data = load_yaml_path(CATALOG_PATH)
    raw = data.get("llm_score_threshold")
    if raw is None:
        raw = data.get("match_threshold")
    try:
        value = float(raw or DEFAULT_LLM_THRESHOLD)
    except (TypeError, ValueError):
        value = DEFAULT_LLM_THRESHOLD
    return max(0.0, min(1.0, value))


def known_skill_ids() -> tuple[str, ...]:
    """返回目录中全部 skill id。"""
    return tuple(item.id for item in load_catalog())


def catalog_index_text() -> str:
    """给前置路由模型看的短目录（不含 overlay 正文）。"""
    lines = ["Skill catalog (id — when to load):"]
    for item in load_catalog():
        trig = ", ".join(item.triggers[:8])
        lines.append(f"- {item.id}: {item.description}")
        if trig:
            lines.append(f"  cues: {trig}")
    return "\n".join(lines)


def load_overlay(item: StyleSkill) -> str:
    """读取一条 skill 的写法 overlay。"""
    path = (SKILLS_DIR / item.overlay_path).resolve()
    if not str(path).startswith(str(SKILLS_DIR.resolve())):
        raise ValueError(f"overlay 路径越界: {item.overlay_path}")
    if not path.is_file():
        raise FileNotFoundError(f"缺少风格 skill overlay: {path}")
    return path.read_text(encoding="utf-8").strip() + "\n"


def _hit_trigger(haystack: str, trigger: str) -> bool:
    """英文按词边界匹配，中文及其它按子串匹配；否定语境下的触发词不计命中。"""
    needle = trigger.strip()
    if not needle:
        return False
    lowered = haystack.lower()
    if _EN_WORD_RE.match(needle) and re.search(r"[A-Za-z]", needle):
        pattern = r"(?<![A-Za-z0-9])" + re.escape(needle.lower()) + r"(?![A-Za-z0-9])"
        for m in re.finditer(pattern, lowered):
            if not _is_negated_span(haystack, m.start()):
                return True
        return False
    idx = 0
    needle_l = needle.lower()
    while True:
        pos = lowered.find(needle_l, idx)
        if pos < 0:
            return False
        if not _is_negated_span(haystack, pos):
            return True
        idx = pos + max(1, len(needle_l))


def _is_negated_span(text: str, start: int) -> bool:
    """触发词前短窗口内若出现禁止/不要等否定，则视为未请求。"""
    window = (text or "")[max(0, start - 6) : start]
    return bool(re.search(r"(?:禁止|不要|不得|勿|别|非|无)", window))


def score_intent(text: str, item: StyleSkill) -> int:
    """统计一条 skill 在意图/库存文本上的触发次数。"""
    haystack = text or ""
    hits = 0
    for trigger in item.triggers:
        if _hit_trigger(haystack, trigger):
            hits += 1
    return hits


def keyword_route(intent: str, inventory: str | None = None) -> dict[str, int]:
    """关键词预筛：返回 id → 命中数（只含 >0）。"""
    blob = "\n".join(part for part in (intent, inventory) if part)
    scores: dict[str, int] = {}
    for item in load_catalog():
        n = score_intent(blob, item)
        if n:
            scores[item.id] = n
    return scores


def _resolve_skill_id(raw_id: str) -> str | None:
    """把模型返回的 id 规范成目录内合法 id。"""
    sid = (raw_id or "").strip()
    if not sid:
        return None
    key = sid.lower().replace("_", "-")
    aliases = {item.id: item.id for item in load_catalog()}
    aliases.update({item.id.lower(): item.id for item in load_catalog()})
    resolved = aliases.get(sid) or aliases.get(key)
    allowed = set(known_skill_ids())
    return resolved if resolved in allowed else None


def _coerce_score(value: Any) -> float | None:
    """把模型返回的分值规范到 0~1。"""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score > 1.0 and score <= 100.0:
        score /= 100.0
    return max(0.0, min(1.0, score))


def parse_score_response(text: str) -> dict[str, float]:
    """解析前置模型返回的 JSON 匹配分；失败则视为未选。"""
    raw = (text or "").strip()
    if not raw:
        return {}
    payload: Any = None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                payload = None
    if not isinstance(payload, dict):
        return {}

    scores: dict[str, float] = {}
    raw_scores = payload.get("scores")
    if isinstance(raw_scores, dict):
        for sid, value in raw_scores.items():
            resolved = _resolve_skill_id(str(sid))
            score = _coerce_score(value)
            if resolved and score is not None:
                scores[resolved] = score

    for item in payload.get("skills") or []:
        if not isinstance(item, dict):
            continue
        resolved = _resolve_skill_id(str(item.get("id") or item.get("skill") or ""))
        score = _coerce_score(item.get("score"))
        if resolved and score is not None:
            scores[resolved] = score
    return scores


def _parse_json_object(text: str) -> Any:
    """从模型回复中提取 JSON 对象/数组。"""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start_obj, end_obj = raw.find("{"), raw.rfind("}")
        if start_obj >= 0 and end_obj > start_obj:
            try:
                return json.loads(raw[start_obj : end_obj + 1])
            except json.JSONDecodeError:
                pass
        start_arr, end_arr = raw.find("["), raw.rfind("]")
        if start_arr >= 0 and end_arr > start_arr:
            try:
                return json.loads(raw[start_arr : end_arr + 1])
            except json.JSONDecodeError:
                return None
    return None


def parse_classify_scores(text: str) -> dict[str, float]:
    """解析路由模型返回的 {"scores":{id: float}}；非法项丢弃。"""
    payload = _parse_json_object(text)
    if not isinstance(payload, dict):
        return {}
    scores_raw = payload.get("scores")
    out: dict[str, float] = {}
    allowed = set(known_skill_ids())
    aliases = {item.id: item.id for item in load_catalog()}
    aliases.update({item.id.lower(): item.id for item in load_catalog()})
    aliases.update({item.id.replace("-", "_"): item.id for item in load_catalog()})

    items: list[tuple[Any, Any]] = []
    if isinstance(scores_raw, dict):
        items = list(scores_raw.items())
    elif isinstance(scores_raw, list):
        for row in scores_raw:
            if isinstance(row, dict):
                sid = row.get("id") or row.get("skill")
                sc = row.get("score")
                if sid is not None and sc is not None:
                    items.append((sid, sc))
    else:
        return {}

    for sid, sc in items:
        key = str(sid).strip()
        resolved = (
            aliases.get(key)
            or aliases.get(key.lower())
            or aliases.get(key.lower().replace("_", "-"))
        )
        if resolved not in allowed:
            continue
        try:
            val = float(sc)
        except (TypeError, ValueError):
            continue
        val = max(0.0, min(1.0, val))
        out[resolved] = max(out.get(resolved, 0.0), val)
    return out


def parse_classify_response(text: str) -> list[str]:
    """解析旧版 JSON skill 列表；兼容测试与历史调用。"""
    raw = (text or "").strip()
    if not raw:
        return []
    payload: Any = _parse_json_object(raw)
    ids: list[str] = []
    if isinstance(payload, dict):
        ids = _as_str_list(payload.get("skills") or payload.get("skill") or [])
    elif isinstance(payload, list):
        ids = _as_str_list(payload)
    allowed = set(known_skill_ids())
    out: list[str] = []
    for sid in ids:
        resolved = _resolve_skill_id(sid)
        if resolved in allowed and resolved not in out:
            out.append(resolved)
    return out


def _resolve_skill_ids(ids: Iterable[str]) -> list[str]:
    """把模型返回的 id 规范化到 catalog。"""
    allowed = set(known_skill_ids())
    out: list[str] = []
    for sid in ids:
        resolved = _resolve_skill_id(sid)
        if resolved in allowed and resolved not in out:
            out.append(resolved)
    return out


def _rank_keyword_ids(scores: dict[str, int], limit: int) -> list[str]:
    """按关键词命中数降序、目录顺序升序取前 limit 个。"""
    order = {item.id: i for i, item in enumerate(load_catalog())}
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], order.get(kv[0], 999)))
    return [sid for sid, n in ranked if n > 0][:limit]


def _rank_score_ids(scores: dict[str, float]) -> list[tuple[str, float]]:
    """按 LLM 分数降序、目录顺序升序。"""
    order = {item.id: i for i, item in enumerate(load_catalog())}
    return sorted(scores.items(), key=lambda kv: (-kv[1], order.get(kv[0], 999)))


def pick_top1_above_threshold(
    scores: dict[str, float],
    threshold: float,
) -> tuple[list[str], float | None]:
    """取分数最高的一条；若 top1 < threshold 则不选。"""
    ranked = _rank_score_ids(scores)
    if not ranked:
        return [], None
    top_id, top_score = ranked[0]
    if top_score < threshold:
        return [], top_score
    return [top_id], top_score


def _merge_ids(*groups: Iterable[str], limit: int) -> list[str]:
    """保序去重后截断。"""
    out: list[str] = []
    allowed = set(known_skill_ids())
    for group in groups:
        for sid in group:
            key = str(sid).strip()
            if key in allowed and key not in out:
                out.append(key)
            if len(out) >= limit:
                return out
    return out


def classify_with_chat(
    chat: ClassifyFn,
    intent: str,
    inventory: str | None = None,
    *,
    threshold: float | None = None,
) -> dict[str, float]:
    """调用前置模型，返回 id → 语义匹配分（0~1）。"""
    thr = catalog_match_threshold() if threshold is None else float(threshold)
    system = load_prompt("route_skills")
    user_lines = [
        catalog_index_text(),
        "",
        f"Score threshold hint: top1 must be >= {thr:.2f} to be selected by the agent.",
        "",
        "Short intent:",
        (intent or "").strip(),
    ]
    if inventory:
        user_lines.extend(["", "Reference inventory (excerpt):", inventory.strip()[:2000]])
    raw = chat(system, "\n".join(user_lines), stage="route")
    scores = parse_score_response(raw)
    if not scores:
        # 兼容旧 JSON：{"skills":["id"]} → 视为 1.0，再过阈值
        payload = _parse_json_object(raw)
        if isinstance(payload, dict):
            legacy_ids = _resolve_skill_ids(
                _as_str_list(payload.get("skills") or payload.get("skill") or [])
            )
            scores = {sid: 1.0 for sid in legacy_ids}
    return scores


def select_style_skills(
    intent: str,
    *,
    inventory: str | None = None,
    forced: list[str] | None = None,
    router: str = "hybrid",
    classify: ClassifyFn | None = None,
    max_skills: int | None = None,
    match_threshold: float | None = None,
    explicit_style: str | None = None,
    explicit_negatives: list[str] | None = None,
) -> SkillSelection:
    """综合强制指定、关键词与前置模型打分，选出本次要加载的风格 skill。

    router:
    - off: 只用 forced
    - keyword: 只用关键词命中数（本地保留）
    - llm / hybrid: 前置模型为每个 skill 打 0~1 分，取 top1；低于阈值则不选

    explicit_style 非空时：跳过自动路由，只保留 forced（用户显式风格优先，避免
    「纯手绘」误加载 handdrawn-live 等冲突 overlay）。
    """
    mode = normalize_router_mode(router)
    limit = max_skills or catalog_max_skills()
    threshold = catalog_match_threshold() if match_threshold is None else float(match_threshold)
    forced_ids = _merge_ids(forced or [], limit=limit)
    if mode == "off":
        return SkillSelection(ids=forced_ids, source="forced" if forced_ids else "none")

    # T1.5：显式风格覆盖自动 skill 路由
    if (explicit_style or "").strip():
        return SkillSelection(
            ids=forced_ids,
            source="explicit_style" if not forced_ids else "forced+explicit_style",
        )

    if mode == "keyword":
        keyword_hits = keyword_route(intent, inventory)
        keyword_hits = _apply_negative_skill_blocks(keyword_hits, explicit_negatives)
        keyword_ids = _rank_keyword_ids(keyword_hits, limit)
        scores = {sid: min(1.0, hits / 3.0) for sid, hits in keyword_hits.items()}
        source = "keyword"
        if forced_ids and keyword_ids:
            source = "forced+keyword"
        elif forced_ids:
            source = "forced"
        ids = _merge_ids(forced_ids, keyword_ids, limit=limit)
        if not ids:
            source = "none"
        return SkillSelection(ids=ids, source=source, keyword_scores=scores)

    # hybrid / llm：LLM 打分，取 top1 且达阈值
    llm_scores: dict[str, float] = {}
    llm_ids: list[str] = []
    llm_top1: float | None = None
    source = "llm"
    if classify is not None:
        llm_scores = classify_with_chat(classify, intent, inventory, threshold=threshold)
        llm_scores = {
            sid: sc
            for sid, sc in llm_scores.items()
            if sid not in _blocked_skills_from_negatives(explicit_negatives)
        }
        llm_ids, llm_top1 = pick_top1_above_threshold(llm_scores, threshold)

    if forced_ids and llm_ids:
        source = "forced+llm"
    elif forced_ids:
        source = "forced"

    ids = _merge_ids(forced_ids, llm_ids, limit=limit)
    if not ids:
        source = "none"
    return SkillSelection(
        ids=ids,
        source=source,
        llm_scores=llm_scores,
        llm_threshold=threshold,
        llm_top1_score=llm_top1,
    )


def _blocked_skills_from_negatives(explicit_negatives: list[str] | None) -> set[str]:
    """由显式否定推导应屏蔽的 skill id。"""
    blob = " ".join(explicit_negatives or [])
    blocked: set[str] = set()
    if re.search(r"不要实拍|禁止实拍|非实拍", blob):
        blocked.add("handdrawn-live")
    return blocked


def _apply_negative_skill_blocks(
    hits: dict[str, int],
    explicit_negatives: list[str] | None,
) -> dict[str, int]:
    """去掉与显式否定冲突的 keyword 命中。"""
    blocked = _blocked_skills_from_negatives(explicit_negatives)
    if not blocked:
        return hits
    return {sid: n for sid, n in hits.items() if sid not in blocked}


def style_block_for_user(selection: SkillSelection) -> str | None:
    """拼进 expand USER 的风格写法块；未选中则返回 None。"""
    pairs = selection.overlay_pairs()
    if not pairs:
        return None
    catalog = {item.id: item for item in load_catalog()}
    parts = [
        "Loaded style skills (writing methodology only; do not change MiniMax field names):",
        "Priority: user intent medium/style > official H3 structure > these genre notes.",
        "If a loaded skill conflicts with the user's stated medium (e.g. pure sketch vs live-action mix), follow the USER intent and drop the conflicting skill cues.",
        "Do not mention canvas / ratio / resolution.",
        "",
    ]
    for sid, body in pairs:
        item = catalog.get(sid)
        origin = item.origin if item else "unknown"
        note = f" ({origin}"
        if item and item.upstream:
            note += f"; upstream={item.upstream}"
        note += ")"
        parts.extend([f"--- style skill: {sid}{note} ---", body.rstrip(), ""])
    return "\n".join(parts).rstrip() + "\n"
