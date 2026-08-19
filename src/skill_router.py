"""按短意图动态加载风格 skill（底座 h3-prompt-writing 始终另行注入）。

路由对齐 Anthropic skill 元数据先行、正文按需加载：
1. catalog.yaml 的 description/triggers 始终可被前置模型看到
2. 只有命中的 overlay 才拼进 expand / format
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
ROUTER_MODES = ("off", "keyword", "hybrid", "llm")
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


@dataclass
class SkillSelection:
    """一次任务选中的风格 skill 及来源。"""

    ids: list[str] = field(default_factory=list)
    source: str = "none"
    scores: dict[str, int] = field(default_factory=dict)

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
        items.append(
            StyleSkill(
                id=sid,
                title=str(raw.get("title") or sid).strip(),
                overlay_path=overlay,
                description=" ".join(str(raw.get("description") or "").split()),
                triggers=tuple(_as_str_list(raw.get("triggers"))),
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
    """单次任务最多加载的风格 skill 数。"""
    if not CATALOG_PATH.is_file():
        return 2
    data = load_yaml_path(CATALOG_PATH)
    try:
        n = int(data.get("max_skills") or 2)
    except (TypeError, ValueError):
        n = 2
    return max(1, min(3, n))


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
    """英文按词边界匹配，中文及其它按子串匹配。"""
    needle = trigger.strip()
    if not needle:
        return False
    lowered = haystack.lower()
    if _EN_WORD_RE.match(needle) and re.search(r"[A-Za-z]", needle):
        pattern = r"(?<![A-Za-z0-9])" + re.escape(needle.lower()) + r"(?![A-Za-z0-9])"
        return re.search(pattern, lowered) is not None
    return needle.lower() in lowered


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


def parse_classify_response(text: str) -> list[str]:
    """解析前置模型返回的 JSON skill 列表；失败则视为未选。"""
    raw = (text or "").strip()
    if not raw:
        return []
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
    ids: list[str] = []
    if isinstance(payload, dict):
        ids = _as_str_list(payload.get("skills") or payload.get("skill") or [])
    elif isinstance(payload, list):
        ids = _as_str_list(payload)
    allowed = set(known_skill_ids())
    out: list[str] = []
    for sid in ids:
        key = sid.strip().lower().replace("_", "-")
        aliases = {item.id: item.id for item in load_catalog()}
        aliases.update({item.id.lower(): item.id for item in load_catalog()})
        resolved = aliases.get(sid) or aliases.get(key)
        if resolved in allowed and resolved not in out:
            out.append(resolved)
    return out


def _rank_ids(scores: dict[str, int], limit: int) -> list[str]:
    """按命中数降序、目录顺序升序取前 limit 个。"""
    order = {item.id: i for i, item in enumerate(load_catalog())}
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], order.get(kv[0], 999)))
    return [sid for sid, n in ranked if n > 0][:limit]


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
) -> list[str]:
    """调用前置模型，仅根据目录短描述选择 skill。"""
    system = load_prompt("route_skills")
    user_lines = [
        catalog_index_text(),
        "",
        "Short intent:",
        (intent or "").strip(),
    ]
    if inventory:
        user_lines.extend(["", "Reference inventory (excerpt):", inventory.strip()[:2000]])
    raw = chat(system, "\n".join(user_lines), stage="route")
    return parse_classify_response(raw)


def select_style_skills(
    intent: str,
    *,
    inventory: str | None = None,
    forced: list[str] | None = None,
    router: str = "hybrid",
    classify: ClassifyFn | None = None,
    max_skills: int | None = None,
) -> SkillSelection:
    """综合强制指定、关键词与前置模型，选出本次要加载的风格 skill。

    router:
    - off: 只用 forced
    - keyword: 只用关键词
    - llm: 只用前置模型（forced 仍合并）
    - hybrid: 关键词有命中则不再请求模型；否则才走前置模型
    """
    mode = (router or "hybrid").strip().lower()
    if mode not in ROUTER_MODES:
        raise ValueError(f"skill_router 须为 {' / '.join(ROUTER_MODES)}")
    limit = max_skills or catalog_max_skills()
    forced_ids = _merge_ids(forced or [], limit=limit)
    if mode == "off":
        return SkillSelection(ids=forced_ids, source="forced" if forced_ids else "none")

    scores = keyword_route(intent, inventory)
    keyword_ids = _rank_ids(scores, limit)

    llm_ids: list[str] = []
    source = "keyword"
    need_llm = mode == "llm" or (mode == "hybrid" and not keyword_ids)
    if need_llm and classify is not None:
        llm_ids = classify_with_chat(classify, intent, inventory)
        source = "llm" if mode == "llm" or not keyword_ids else "hybrid"

    if forced_ids and (keyword_ids or llm_ids):
        source = "forced+" + source
    elif forced_ids:
        source = "forced"

    ids = _merge_ids(forced_ids, keyword_ids, llm_ids, limit=limit)
    if not ids:
        source = "none"
    return SkillSelection(ids=ids, source=source, scores=scores)


def style_block_for_user(selection: SkillSelection) -> str | None:
    """拼进 expand USER 的风格写法块；未选中则返回 None。"""
    pairs = selection.overlay_pairs()
    if not pairs:
        return None
    parts = [
        "Loaded style skills (writing methodology only; do not change MiniMax field names):",
        "Priority: official H3 structure > these genre notes. Do not mention canvas / ratio / resolution.",
        "",
    ]
    for sid, body in pairs:
        parts.extend([f"--- style skill: {sid} ---", body.rstrip(), ""])
    return "\n".join(parts).rstrip() + "\n"
