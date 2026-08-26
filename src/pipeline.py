"""T2VA / I2VA / FL2VA / L2VA / R2VA 多步 Gemini 编排；格式化注入官方 skill 指南。"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ROOT, gemini_settings, h3_settings, load_prompt
from .contract import parse_intent
from .gemini import chat
from .media import user_parts
from .report import write_report
from .skill import (
    ALL_MODES,
    GRID_SCAN_INSTRUCTION,
    KEYFRAME_MODES,
    compose_format_system,
    ensure_alignment_prefix,
    expand_hint,
    grid_coverage_gap,
    grid_keep_subjects_note,
)
from .mechanism_router import (
    ROUTER_MODES as MECHANISM_ROUTER_MODES,
    mechanism_block_for_user,
    select_mechanisms,
    writing_blocks_for_user,
)
from .skill_router import normalize_router_mode, select_style_skills, style_block_for_user
from .verify import (
    extract_locked_dialogue,
    intent_allows_multi_shot,
    intent_allows_na_music,
    verify_and_fix,
)
from .video import generate_video

CANVAS_RE = re.compile(
    r"(?:,\s*)?(?:\d+:\d+[ \t]*)?(?:aspect ratio|canvas size|resolution|帧率|画幅)[^.;，。\n]*|"
    r"(?:16:9|9:16|21:9|4:3|3:4|1:1)[ \t]*(?:aspect ratio|横屏|竖屏)?|"
    r"\b(?:768P|2K|1280x720|1920x1080|\d+[ \t]*fps)\b",
    re.I,
)
DURATION_RE = re.compile(r"(?:约|大概)?\s*(\d{1,2})\s*秒")


def infer_duration(intent: str, fallback: int = 5) -> int:
    """从短意图里的「约 N 秒」推断时长；夹到 4–15。"""
    m = DURATION_RE.search(intent or "")
    n = int(m.group(1)) if m else fallback
    return max(4, min(15, n))


def strip_canvas(text: str) -> str:
    """去掉误写入字段的画幅/分辨率/帧率，保留段落换行结构。"""
    cleaned = CANVAS_RE.sub(" ", text)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n[ \t]+", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip() + "\n"


def _shot_policy_block(intent: str) -> str:
    """根据意图注入单镜/切镜写作提醒。"""
    text = intent or ""
    if re.search(r"多镜头|分镜|切到|第二镜|montage|\bcuts?\b", text, re.I):
        return (
            "SHOT POLICY: multi-shot allowed as stated in the intent; "
            "keep cut count minimal and motivated."
        )
    if re.search(r"禁止切镜|不得切镜|不要切镜|单镜头|一镜到底", text):
        return (
            "SHOT POLICY: exactly ONE continuous shot only. Do not invent cuts, "
            "montages, or [Shot 2]+."
        )
    return (
        "SHOT POLICY: prefer exactly ONE continuous shot unless the intent "
        "explicitly asks for cuts / 分镜 / multiple shots."
    )


def _music_policy_block(intent: str) -> str:
    """根据意图注入配乐写作提醒。"""
    text = intent or ""
    if re.search(r"不要配乐|禁止配乐|无配乐|不要音乐|不要非叙境", text):
        return (
            "MUSIC POLICY: non_diegetic_music must be N/A. "
            "Do not invent score, theme, or underscore."
        )
    return (
        "MUSIC POLICY: only add non_diegetic music when the intent asks for it; "
        "otherwise prefer N/A. Do not write N/A for overall_soundscape when diegetic sound exists."
    )

def _locked_dialogue_block(
    intent: str,
    contract: Any | None = None,
) -> str | None:
    """把用户意图里的锁定台词/屏上字列成清单；优先用 IntentContract（含 LLM 分类）。"""
    spoken: list[str] = []
    onscreen: list[str] = []
    if contract is not None:
        spoken = [
            d.text
            for d in (getattr(contract, "dialogue", None) or [])
            if getattr(d, "text", None)
        ]
        onscreen = list(getattr(contract, "onscreen_text", None) or [])
    else:
        from .verify import extract_locked_dialogue, extract_locked_onscreen

        spoken = extract_locked_dialogue(intent)
        onscreen = extract_locked_onscreen(intent)
    parts: list[str] = []
    if spoken:
        body = "\n".join(f"- {line}" for line in spoken)
        parts.append(
            "Locked spoken lines from the user's intent. Copy each line verbatim in the original language. "
            "Do not translate, paraphrase, or add an English gloss. Never use [Mandarin]; Chinese lines use [Chinese].\n"
            + body
        )
    if onscreen:
        body = "\n".join(f"- {line}" for line in onscreen)
        parts.append(
            "Locked on-screen lines from the user's intent. Keep each line verbatim as on-screen text "
            "in the original language. Do not translate or invent extra captions.\n"
            + body
        )
    if not parts:
        return None
    return "\n\n".join(parts)


def _expand_user(
    intent: str,
    *,
    inventory: str | None = None,
    mode: str,
    writing_block: str | None = None,
    contract_block: str | None = None,
    contract: Any | None = None,
) -> str:
    """构造扩写 USER：短意图 + Intent Contract + 官方模式写作路径 + 可选库存与写法块。"""
    lines = [
        f"Mode: {mode}. Expand the short intent. Do not output MiniMax fields yet.",
        f"Writing path: {expand_hint(mode)}",
        "",
        "Short intent:",
        intent.strip(),
    ]
    if contract_block:
        lines.extend(["", contract_block.strip()])
    locked = _locked_dialogue_block(intent, contract=contract)
    if locked:
        lines.extend(["", locked])
    if inventory:
        lines.extend(["", "Reference inventory:", inventory.strip()])
        keep = grid_keep_subjects_note(inventory)
        if keep:
            lines.extend(["", keep])
    if writing_block:
        lines.extend(["", writing_block.rstrip()])
    return "\n".join(lines)


def _format_user(
    mode: str,
    scene: str,
    *,
    inventory: str | None,
    duration: int | None,
    intent: str = "",
    contract_block: str | None = None,
    complexity_block: str | None = None,
    contract: Any | None = None,
) -> str:
    """构造共用格式化 USER：模式 + Intent Contract + 复杂度预算 + 场景稿 + 可选库存。"""
    lines = [
        f"MODE={mode}",
        "Serialize the scene note into the MiniMax-H3 fields for this MODE.",
        "Follow the appended official writing guide. Do not mention aspect ratio, resolution, fps, or canvas size.",
        "Carry forward densified cinematic detail from the scene note; do not shrink a FILMIC / "
        "COMPLEXITY BUDGET note below its word-count floor.",
        _shot_policy_block(intent),
        _music_policy_block(intent),
    ]
    if duration is not None:
        lines.append(
            f"Duration hint: {duration:g} seconds. Keep cut timestamps inside this length. "
            "Do not write the duration into the core fields. "
            f"If MODE is fl2va or l2va, the alignment line MUST use S.SS = {float(duration):.2f}."
        )
    if contract_block:
        lines.extend(["", contract_block.strip()])
    if complexity_block:
        lines.extend(["", complexity_block.strip()])
    lines.extend(["", "Scene note:", scene.strip()])
    locked = _locked_dialogue_block(intent, contract=contract)
    if locked:
        lines.extend(["", locked])
    if inventory:
        lines.extend(["", "Reference inventory:", inventory.strip()])
        keep = grid_keep_subjects_note(inventory)
        if keep:
            lines.extend(["", keep])
    return "\n".join(lines)


def _append_grid_scan(text: str) -> str:
    """在感知 USER 文本末尾加上宫格扫全说明。"""
    return text.rstrip() + "\n\n" + GRID_SCAN_INSTRUCTION


def _rescan_if_grid_incomplete(
    system: str,
    inventory: str,
    *,
    text: str,
    images: list[str] | None,
    videos: list[str] | None = None,
    audios: list[str] | None = None,
) -> tuple[str, str | None]:
    """宫格声明与格子笔记不一致时再扫一次；返回 (库存, 补扫阶段名或 None)。"""
    gap = grid_coverage_gap(inventory)
    if not gap:
        return inventory, None
    follow = (
        f"{text}\n\nPrevious inventory (incomplete):\n{inventory.strip()}\n\n{gap}"
    )
    scanned = chat(
        system,
        user_parts(follow, images=images, videos=videos, audios=audios),
        stage="perceive",
    )
    return scanned, "perceive_grid_rescan"


def _perceive_keyframes(
    mode: str,
    *,
    first_frame: str | None,
    last_frame: str | None,
    duration: int,
) -> tuple[str, str | None]:
    """对 I2VA/FL2VA/L2VA 的静帧做事实库存；宫格漏格时补扫。"""
    system = load_prompt("perceive_image")
    if mode == "i2va":
        text = (
            "Mode: I2VA. Attached image is <Picture 1>, the FIRST frame at 0.00s / [Shot 1]. "
            "Describe visible facts only."
        )
        images = [first_frame] if first_frame else None
    elif mode == "fl2va":
        text = (
            "Mode: FL2VA. Two images in order:\n"
            "<Picture 1> = FIRST frame at 0.00s (attached first).\n"
            f"<Picture 2> = LAST frame at {float(duration):.2f}s (attached second).\n"
            "Describe each image separately. Do not invent the path between them."
        )
        images = [p for p in (first_frame, last_frame) if p]
    else:
        text = (
            "Mode: L2VA. Attached image is <Picture 1>, the LAST frame of the clip "
            f"(lands at about {float(duration):.2f}s). It does NOT belong to Shot 1. "
            "Describe the landing state only."
        )
        images = [last_frame] if last_frame else None
    text = _append_grid_scan(text)
    inventory = chat(system, user_parts(text, images=images), stage="perceive")
    return _rescan_if_grid_incomplete(system, inventory, text=text, images=images)


def _elaborate_user(
    expanded: str,
    inventory: str | None,
    intent: str = "",
    *,
    contract_block: str | None = None,
    complexity_block: str | None = None,
    contract: Any | None = None,
) -> str:
    """构造补细节 USER：Intent Contract + 复杂度预算 + 扩写稿 + 可选库存。"""
    lines = [
        "Make the scene note below concrete and physically plausible. "
        "Match detail depth to the COMPLEXITY BUDGET when provided. "
        "If FILMIC SINGLE-SHOT is set, target the UPPER half of the budget; "
        "otherwise do not pad a simple single-shot clip past the ceiling.",
        _shot_policy_block(intent),
        _music_policy_block(intent),
    ]
    if contract_block:
        lines.extend(["", contract_block.strip()])
    if complexity_block:
        lines.extend(["", complexity_block.strip()])
    if inventory:
        lines.extend(["", "Reference inventory:", inventory.strip()])
    lines.extend(["", "Scene note:", expanded.strip()])
    locked = _locked_dialogue_block(intent, contract=contract)
    if locked:
        lines.extend(["", locked])
    return "\n".join(lines)


def enhance(
    mode: str,
    intent: str,
    *,
    first_frame: str | None = None,
    last_frame: str | None = None,
    reference_images: list[str] | None = None,
    reference_videos: list[str] | None = None,
    reference_audios: list[str] | None = None,
    duration: int | None = None,
    out_dir: Path | None = None,
    skills: list[str] | None = None,
    skill_router: str = "hybrid",
    mechanisms: list[str] | None = None,
    mechanism_router: str = "hybrid",
    enable_verify: bool = True,
    verify_intent_llm: bool | None = None,
) -> dict[str, Any]:
    """
    跑完感知（若需要）→ 风格/机制路由 → 扩写 → 补细节 → 注入官方指南后格式化。

    skills: 强制加载的风格 skill id。
    skill_router: off / keyword / hybrid / llm。
    hybrid / llm：前置模型为各 skill 打 0~1 分，取 top1（默认阈值 0.6）；
    keyword：只用触发词命中；off：只用强制 id。
    mechanisms: 强制加载的 T8 Creative DNA 机制 id。
    mechanism_router: 机制路由模式，默认同 skill_router（机制侧仍为关键词优先 hybrid）。

    Returns:
        含 prompt、各步原文、mode
    """
    mode = mode.lower().strip()
    if mode not in ALL_MODES:
        raise ValueError(f"mode 须为 {' / '.join(ALL_MODES)}")
    intent = (intent or "").strip()
    if not intent:
        raise ValueError("短意图为空")
    t0 = time.perf_counter()

    images = list(reference_images or [])
    videos = list(reference_videos or [])
    audios = list(reference_audios or [])
    if mode == "i2va" and not first_frame:
        raise ValueError("i2va 需要 --first-frame")
    if mode == "fl2va" and (not first_frame or not last_frame):
        raise ValueError("fl2va 需要同时提供 --first-frame 与 --last-frame")
    if mode == "l2va" and not last_frame:
        raise ValueError("l2va 需要 --last-frame")
    if mode == "r2va":
        if not images and not videos:
            raise ValueError("r2va 须至少 1 张参考图或 1 段参考视频")
        if len(images) > 9:
            raise ValueError("r2va 参考图数量 ≤ 9")
        if len(videos) > 3:
            raise ValueError("r2va 参考视频数量 ≤ 3")
        if len(audios) > 3:
            raise ValueError("r2va 参考音频数量 ≤ 3")

    dur = duration if duration is not None else infer_duration(intent)
    steps: list[dict[str, str]] = []
    inventory: str | None = None

    if mode in KEYFRAME_MODES:
        inventory, rescan = _perceive_keyframes(
            mode,
            first_frame=first_frame,
            last_frame=last_frame,
            duration=dur,
        )
        steps.append({"stage": "perceive_image", "text": inventory})
        if rescan:
            steps.append({"stage": rescan, "text": inventory})
    elif mode == "r2va":
        labels = []
        for i, p in enumerate(images, 1):
            labels.append(f"<Picture {i}> = {p}")
        for i, p in enumerate(videos, 1):
            labels.append(f"<Video {i}> = {p}")
        for i, p in enumerate(audios, 1):
            labels.append(f"<Audio {i}> = {p}")
        system = load_prompt("perceive_refs")
        text = _append_grid_scan(
            "Inventory attached assets in this order:\n" + "\n".join(labels)
        )
        inventory = chat(
            system,
            user_parts(
                text,
                images=images or None,
                videos=videos or None,
                audios=audios or None,
            ),
            stage="perceive",
        )
        steps.append({"stage": "perceive_refs", "text": inventory})
        rescanned, rescan = _rescan_if_grid_incomplete(
            system,
            inventory,
            text=text,
            images=images or None,
            videos=videos or None,
            audios=audios or None,
        )
        if rescan:
            inventory = rescanned
            steps.append({"stage": rescan, "text": inventory})

    # Intent Contract：感知后、路由前；只抽取不推断。契约写入 run.json，本步不改三段 prompt。
    contract = parse_intent(intent, mode=mode, chat=chat, use_llm=True)
    steps.append({"stage": "contract", "text": contract.format_for_prompt()})
    # 时长：用户显式秒数优先（contract 已夹到 4–15）
    if duration is None and contract.duration_sec:
        dur = int(contract.duration_sec)

    router_mode = normalize_router_mode(skill_router or "hybrid")
    style_sel = select_style_skills(
        intent,
        inventory=inventory,
        forced=skills,
        router=router_mode,
        classify=chat if router_mode in {"hybrid", "llm"} else None,
        explicit_style=contract.explicit_style,
        explicit_negatives=list(contract.explicit_negatives or []),
    )
    style_block = style_block_for_user(style_sel)
    extra_guides = style_sel.overlay_pairs()
    if style_sel.ids or style_sel.scores:
        steps.append(
            {
                "stage": "skill_route",
                "source": style_sel.source,
                "skills": style_sel.ids,
                "scores": style_sel.scores,
                "threshold": style_sel.threshold,
            }
        )

    mech_router_mode = (mechanism_router or "hybrid").strip().lower()
    if mech_router_mode not in MECHANISM_ROUTER_MODES:
        raise ValueError(f"mechanism_router 须为 {' / '.join(MECHANISM_ROUTER_MODES)}")
    mech_sel = select_mechanisms(
        intent,
        inventory=inventory,
        forced=mechanisms,
        router=mech_router_mode,
        classify=chat if mech_router_mode in {"hybrid", "llm"} else None,
    )
    mechanism_block = mechanism_block_for_user(mech_sel)
    writing_block = writing_blocks_for_user(style_block, mechanism_block)
    if mech_sel.ids:
        steps.append(
            {
                "stage": "mechanism_route",
                "text": f"source={mech_sel.source}; mechanisms={', '.join(mech_sel.ids)}",
            }
        )

    contract_block = contract.format_for_prompt()

    expand_sys = load_prompt("expand_intent")
    expanded = chat(
        expand_sys,
        _expand_user(
            intent,
            inventory=inventory,
            mode=mode,
            writing_block=writing_block,
            contract_block=contract_block,
            contract=contract,
        ),
        stage="expand",
    )
    steps.append({"stage": "expand", "text": expanded})

    # 补细节：把扩写稿提升到官方 Context-IR 的详略级别（散文，未进字段）。
    elaborate_sys = load_prompt("elaborate")
    from .complexity import (
        complexity_word_budget,
        densify_user_block,
        format_complexity_budget_block,
        has_filmic_locks,
    )
    from .enrichment import _word_count

    complexity_block = format_complexity_budget_block(contract)
    elaborated = chat(
        elaborate_sys,
        _elaborate_user(
            expanded,
            inventory,
            intent=intent,
            contract_block=contract_block,
            complexity_block=complexity_block,
            contract=contract,
        ),
        stage="elaborate",
    )
    steps.append({"stage": "elaborate", "text": elaborated})

    # filmic 欠词：额外 densify 一轮，逼近官方 cinematic 词数密度
    lo, hi = complexity_word_budget(contract)
    if has_filmic_locks(contract) and _word_count(elaborated) < lo:
        densify_sys = load_prompt("densify_filmic")
        elaborated = chat(
            densify_sys,
            densify_user_block(
                elaborated,
                contract_block=contract_block,
                complexity_block=complexity_block,
                lo=lo,
                hi=hi,
                inventory=inventory,
            ),
            stage="densify_filmic",
        )
        steps.append({"stage": "densify_filmic", "text": elaborated})

    format_sys = compose_format_system(mode, load_prompt("format_h3"), extra_guides)
    # 格式化直接消费补细节稿（已含构图/镜头/声音/音乐细节），保证成果进入最终提示词。
    format_text = _format_user(
        mode,
        elaborated,
        inventory=inventory,
        duration=dur,
        intent=intent,
        contract_block=contract_block,
        complexity_block=complexity_block,
        contract=contract,
    )
    if mode in KEYFRAME_MODES:
        frame_images = [p for p in (first_frame, last_frame) if p]
        format_user: str | list[dict[str, Any]] = user_parts(
            format_text + "\n\nStill frames are re-attached so you can keep identity and layout.",
            images=frame_images,
        )
    else:
        format_user = format_text
    raw_prompt = chat(format_sys, format_user, stage="format")
    # official_prompt(raw)：上游格式化模型的原始输出（未清洗）。
    official_prompt = raw_prompt
    # local_prompt(cleaned)：去画幅残留，并按官方指南规范关键帧对齐句。
    prompt = ensure_alignment_prefix(mode, strip_canvas(raw_prompt), dur)
    steps.append({"stage": "format", "text": prompt})

    # 质量校验：规则硬校验，存在 error 时自动 LLM 修复一次。
    verify_cfg = gemini_settings().get("verify") or {}
    max_fix_rounds = int(verify_cfg.get("max_fix_rounds", 1)) if enable_verify else 0
    if verify_intent_llm is None:
        verify_intent_llm = bool(verify_cfg.get("intent_llm", False))
    if mode in KEYFRAME_MODES:
        frame_images = [p for p in (first_frame, last_frame) if p]
        verify_imgs, verify_vids, verify_auds = len(frame_images), 0, 0
    else:
        verify_imgs = len(images) if mode == "r2va" else 0
        verify_vids = len(videos) if mode == "r2va" else 0
        verify_auds = len(audios) if mode == "r2va" else 0
    verify_result = verify_and_fix(
        mode,
        prompt,
        duration=dur,
        images=verify_imgs,
        videos=verify_vids,
        audios=verify_auds,
        chat=chat if enable_verify else None,
        intent=intent,
        inventory=inventory,
        check_intent_llm=bool(verify_intent_llm and enable_verify),
        max_fix_rounds=max_fix_rounds,
        contract=contract,
        max_fidelity_fix_rounds=2 if enable_verify else 0,
    )
    if verify_result["prompt"] != prompt:
        prompt = verify_result["prompt"]
        stage_name = (
            "verify_fidelity_fix"
            if int(verify_result.get("fidelity_rounds") or 0) > 0
            else "verify_fix"
        )
        steps.append({"stage": stage_name, "text": prompt})

    record: dict[str, Any] = {
        "mode": mode,
        "intent": intent,
        "duration": dur,
        "enhance_elapsed_sec": round(time.perf_counter() - t0, 3),
        "first_frame": first_frame,
        "last_frame": last_frame,
        "reference_images": images if mode == "r2va" else [],
        "i2va_first_frame": first_frame if mode == "i2va" else None,
        "reference_videos": videos,
        "reference_audios": audios,
        "inventory": inventory,
        "contract": contract.to_dict(),
        "expanded": expanded,
        "elaborated": elaborated,
        "style_skills": style_sel.ids,
        "style_skill_source": style_sel.source,
        "style_skill_scores": style_sel.scores,
        "style_skill_threshold": style_sel.threshold,
        "mechanisms": mech_sel.ids,
        "mechanism_source": mech_sel.source,
        "skills": {
            "core": ["h3-prompt-writing"],
            "style": list(style_sel.ids),
            "style_source": style_sel.source,
            "style_detail": style_sel.detail_records(),
            "style_llm_route": style_sel.llm_route_meta(),
            "mechanisms": list(mech_sel.ids),
            "mechanism_source": mech_sel.source,
        },
        "prompt_official": official_prompt,
        "prompt": prompt,
        "verify": verify_result,
        "steps": steps,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        (out_dir / "prompt_official_raw.txt").write_text(
            official_prompt.strip() + "\n",
            encoding="utf-8",
        )
        (out_dir / "expanded.txt").write_text(expanded.strip() + "\n", encoding="utf-8")
        (out_dir / "elaborated.txt").write_text(elaborated.strip() + "\n", encoding="utf-8")
        if inventory:
            (out_dir / "inventory.txt").write_text(inventory.strip() + "\n", encoding="utf-8")
        (out_dir / "contract.json").write_text(
            json.dumps(contract.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        slim = {k: v for k, v in record.items() if k != "steps"}
        slim["steps"] = steps
        (out_dir / "run.json").write_text(
            json.dumps(slim, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        record["out_dir"] = str(out_dir)
    return record


def run_job(
    mode: str,
    intent: str,
    *,
    first_frame: str | None = None,
    last_frame: str | None = None,
    reference_images: list[str] | None = None,
    reference_videos: list[str] | None = None,
    reference_audios: list[str] | None = None,
    duration: int | None = None,
    ratio: str | None = None,
    resolution: str | None = None,
    out_dir: Path | None = None,
    make_video: bool = True,
    wait_video: bool = True,
    compare_video: bool = False,
    skills: list[str] | None = None,
    skill_router: str = "hybrid",
    mechanisms: list[str] | None = None,
    mechanism_router: str = "hybrid",
    enable_verify: bool = True,
    verify_intent_llm: bool | None = None,
) -> dict[str, Any]:
    """增强 prompt，可选调用 H3 出片。画幅/分辨率只进视频 API。"""
    h3 = h3_settings()
    dur = duration if duration is not None else infer_duration(intent, h3["default_duration"])
    if out_dir is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = ROOT / "runs" / f"{mode}_{stamp}"
    rec = enhance(
        mode,
        intent,
        first_frame=first_frame,
        last_frame=last_frame,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
        duration=dur,
        out_dir=out_dir,
        skills=skills,
        skill_router=skill_router,
        mechanisms=mechanisms,
        mechanism_router=mechanism_router,
        enable_verify=enable_verify,
        verify_intent_llm=verify_intent_llm,
    )
    rec["ratio_api"] = ratio or (h3["default_ratio"] if mode == "t2va" else "adaptive")
    rec["resolution_api"] = resolution or h3["default_resolution"]
    rec["make_video"] = make_video
    rec["compare_video"] = compare_video

    prompt_official = rec.get("prompt_official") or ""
    prompt_local = rec.get("prompt") or ""
    video_official: dict[str, Any] | None = None
    video_local: dict[str, Any] | None = None
    if make_video:
        video_local_path = Path(out_dir) / "out_local.mp4"
        video_local_res = generate_video(
            mode,
            prompt_local,
            duration=dur,
            ratio=ratio,
            resolution=resolution,
            first_frame=first_frame,
            last_frame=last_frame,
            reference_images=reference_images,
            reference_videos=reference_videos,
            reference_audios=reference_audios,
            output=video_local_path,
            wait=wait_video,
        )
        video_local = {k: v for k, v in video_local_res.items() if k != "task"}
        video_local["task_status"] = (video_local_res.get("task") or {}).get("status")
        rec["video"] = video_local

        if compare_video:
            video_official_path = Path(out_dir) / "out_official.mp4"
            video_official_res = generate_video(
                mode,
                prompt_official,
                duration=dur,
                ratio=ratio,
                resolution=resolution,
                first_frame=first_frame,
                last_frame=last_frame,
                reference_images=reference_images,
                reference_videos=reference_videos,
                reference_audios=reference_audios,
                output=video_official_path,
                wait=wait_video,
            )
            video_official = {k: v for k, v in video_official_res.items() if k != "task"}
            video_official["task_status"] = (video_official_res.get("task") or {}).get("status")
            rec["video_official"] = video_official

        run_path = Path(out_dir) / "run.json"
        if run_path.is_file():
            dumped = json.loads(run_path.read_text(encoding="utf-8"))
            dumped["video"] = rec.get("video")
            dumped["video_official"] = rec.get("video_official")
            dumped["ratio_api"] = rec["ratio_api"]
            dumped["resolution_api"] = rec["resolution_api"]
            run_path.write_text(json.dumps(dumped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 无论是否出片，都写出提示词对比报告（可选视频对比会包含对应视频结果）。
    write_report(
        out_dir,
        record=rec,
        prompt_official=prompt_official,
        prompt_local=prompt_local,
        video_official=video_official,
        video_local=video_local,
    )
    return rec
