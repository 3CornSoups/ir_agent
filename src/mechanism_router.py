"""T8 Creative DNA 机制路由：在扩写/补细节前按需加载因果锚点 overlay。

与题材 style skill 正交：style 管画风/体裁，mechanism 管节拍与因果结构。
参考：https://github.com/T8mars/minimax-h3-prompt-skill-T8 @ v1.1.8
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Iterable

from .config import ROOT, load_prompt
from .skill_router import _as_str_list, load_yaml_path, score_intent

T8_DIR = ROOT / "skills" / "t8"
CATALOG_PATH = T8_DIR / "catalog.yaml"
ROUTER_MODES = ("off", "keyword", "hybrid", "llm")
ClassifyFn = Callable[..., str]


@dataclass(frozen=True)
class MechanismSkill:
    """一条 T8 Creative DNA 机制。"""

    id: str
    title: str
    overlay_path: str
    description: str
    triggers: tuple[str, ...] = ()


@dataclass
class MechanismSelection:
    """一次任务选中的机制及来源。"""

    ids: list[str] = field(default_factory=list)
    source: str = "none"
    scores: dict[str, int] = field(default_factory=dict)

    def overlay_pairs(self) -> list[tuple[str, str]]:
        """返回 (id, overlay 正文) 供扩写/补细节 USER 拼接。"""
        catalog = {item.id: item for item in load_catalog()}
        pairs: list[tuple[str, str]] = []
        for mid in self.ids:
            item = catalog.get(mid)
            if item is None:
                continue
            pairs.append((mid, load_overlay(item)))
        return pairs


@lru_cache(maxsize=1)
def load_catalog() -> tuple[MechanismSkill, ...]:
    """读取 skills/t8/catalog.yaml；缺文件时返回空目录。"""
    if not CATALOG_PATH.is_file():
        return ()
    data = load_yaml_path(CATALOG_PATH)
    items: list[MechanismSkill] = []
    for raw in data.get("skills") or []:
        if not isinstance(raw, dict):
            continue
        mid = str(raw.get("id") or "").strip()
        overlay = str(raw.get("overlay") or "").strip()
        if not mid or not overlay:
            continue
        items.append(
            MechanismSkill(
                id=mid,
                title=str(raw.get("title") or mid).strip(),
                overlay_path=overlay,
                description=" ".join(str(raw.get("description") or "").split()),
                triggers=tuple(_as_str_list(raw.get("triggers"))),
            )
        )
    return tuple(items)


def catalog_max_mechanisms() -> int:
    """单次任务最多加载的机制数。"""
    if not CATALOG_PATH.is_file():
        return 2
    data = load_yaml_path(CATALOG_PATH)
    try:
        n = int(data.get("max_mechanisms") or 2)
    except (TypeError, ValueError):
        n = 2
    return max(1, min(3, n))


def known_mechanism_ids() -> tuple[str, ...]:
    """返回目录中全部机制 id。"""
    return tuple(item.id for item in load_catalog())


def catalog_index_text() -> str:
    """给前置路由模型看的短目录（不含 overlay 正文）。"""
    lines = ["T8 mechanism catalog (id — when to load):"]
    for item in load_catalog():
        trig = ", ".join(item.triggers[:8])
        lines.append(f"- {item.id}: {item.description}")
        if trig:
            lines.append(f"  cues: {trig}")
    return "\n".join(lines)


def load_overlay(item: MechanismSkill) -> str:
    """读取一条机制的写法 overlay。"""
    path = (T8_DIR / item.overlay_path).resolve()
    if not str(path).startswith(str(T8_DIR.resolve())):
        raise ValueError(f"overlay 路径越界: {item.overlay_path}")
    if not path.is_file():
        raise FileNotFoundError(f"缺少 T8 机制 overlay: {path}")
    return path.read_text(encoding="utf-8").strip() + "\n"


def keyword_route(intent: str, inventory: str | None = None) -> dict[str, int]:
    """关键词预筛：返回 id → 命中数（只含 >0）。"""
    blob = "\n".join(part for part in (intent, inventory) if part)
    scores: dict[str, int] = {}
    for item in load_catalog():
        n = score_intent(blob, item)  # type: ignore[arg-type]
        if n:
            scores[item.id] = n
    return scores


def parse_classify_response(text: str) -> list[str]:
    """解析前置模型返回的 JSON 机制列表；失败则视为未选。"""
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
        ids = _as_str_list(
            payload.get("mechanisms")
            or payload.get("mechanism")
            or payload.get("skills")
            or payload.get("skill")
            or []
        )
    elif isinstance(payload, list):
        ids = _as_str_list(payload)
    allowed = set(known_mechanism_ids())
    out: list[str] = []
    for mid in ids:
        key = mid.strip().lower().replace("_", "-")
        aliases = {item.id: item.id for item in load_catalog()}
        aliases.update({item.id.lower(): item.id for item in load_catalog()})
        resolved = aliases.get(mid) or aliases.get(key)
        if resolved in allowed and resolved not in out:
            out.append(resolved)
    return out


def _rank_ids(scores: dict[str, int], limit: int) -> list[str]:
    """按命中数降序、目录顺序升序取前 limit 个。"""
    order = {item.id: i for i, item in enumerate(load_catalog())}
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], order.get(kv[0], 999)))
    return [mid for mid, n in ranked if n > 0][:limit]


def _merge_ids(*groups: Iterable[str], limit: int) -> list[str]:
    """保序去重后截断。"""
    out: list[str] = []
    allowed = set(known_mechanism_ids())
    for group in groups:
        for mid in group:
            key = str(mid).strip()
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
    """调用前置模型，仅根据目录短描述选择机制。"""
    system = load_prompt("route_mechanisms")
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


def select_mechanisms(
    intent: str,
    *,
    inventory: str | None = None,
    forced: list[str] | None = None,
    router: str = "hybrid",
    classify: ClassifyFn | None = None,
    max_mechanisms: int | None = None,
) -> MechanismSelection:
    """综合强制指定、关键词与前置模型，选出本次要加载的 T8 机制。

    router:
    - off: 只用 forced
    - keyword: 只用关键词
    - llm: 只用前置模型（forced 仍合并）
    - hybrid: 关键词有命中则不再请求模型；否则才走前置模型
    """
    mode = (router or "hybrid").strip().lower()
    if mode not in ROUTER_MODES:
        raise ValueError(f"mechanism_router 须为 {' / '.join(ROUTER_MODES)}")
    limit = max_mechanisms or catalog_max_mechanisms()
    forced_ids = _merge_ids(forced or [], limit=limit)
    if mode == "off":
        return MechanismSelection(ids=forced_ids, source="forced" if forced_ids else "none")

    scores = keyword_route(intent, inventory)
    keyword_ids = _rank_ids({k: v for k, v in scores.items() if v >= 2}, limit)

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
    return MechanismSelection(ids=ids, source=source, scores=scores)


def mechanism_block_for_user(selection: MechanismSelection) -> str | None:
    """拼进扩写/补细节 USER 的机制写法块；未选中则返回 None。"""
    pairs = selection.overlay_pairs()
    if not pairs:
        return None
    parts = [
        "Loaded T8 Creative DNA mechanisms (beat/causal structure only; not final H3 fields):",
        "Priority: user intent > these anchors > genre style. Do not mention canvas / ratio / resolution.",
        "",
    ]
    for mid, body in pairs:
        parts.extend([f"--- mechanism: {mid} ---", body.rstrip(), ""])
    return "\n".join(parts).rstrip() + "\n"


def writing_blocks_for_user(
    style_block: str | None,
    mechanism_block: str | None,
) -> str | None:
    """合并题材 style 与 T8 mechanism 写法块，供扩写/补细节共用。"""
    chunks = [b.strip() for b in (style_block, mechanism_block) if b and b.strip()]
    if not chunks:
        return None
    return "\n\n".join(chunks) + "\n"
