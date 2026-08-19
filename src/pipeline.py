"""T2VA / I2VA / FL2VA / L2VA / R2VA 多步 Gemini 编排；格式化注入官方 skill 指南。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ROOT, gemini_settings, h3_settings, load_prompt
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
from .skill_router import ROUTER_MODES, select_style_skills, style_block_for_user
from .verify import verify_and_fix
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


def _expand_user(
    intent: str,
    *,
    inventory: str | None = None,
    mode: str,
    style_block: str | None = None,
) -> str:
    """构造扩写 USER：短意图 + 官方模式写作路径 + 可选库存与风格 skill。"""
    lines = [
        f"Mode: {mode}. Expand the short intent. Do not output MiniMax fields yet.",
        f"Writing path: {expand_hint(mode)}",
        "",
        "Short intent:",
        intent.strip(),
    ]
    if inventory:
        lines.extend(["", "Reference inventory:", inventory.strip()])
        keep = grid_keep_subjects_note(inventory)
        if keep:
            lines.extend(["", keep])
    if style_block:
        lines.extend(["", style_block.rstrip()])
    return "\n".join(lines)


def _format_user(
    mode: str,
    scene: str,
    *,
    inventory: str | None,
    duration: int | None,
) -> str:
    """构造共用格式化 USER：模式 + 场景稿 + 可选库存与时长约束。"""
    lines = [
        f"MODE={mode}",
        "Serialize the scene note into the MiniMax-H3 fields for this MODE.",
        "Follow the appended official writing guide. Do not mention aspect ratio, resolution, fps, or canvas size.",
    ]
    if duration is not None:
        lines.append(
            f"Duration hint: {duration:g} seconds. Keep cut timestamps inside this length. "
            "Do not write the duration into the core fields. "
            f"If MODE is fl2va or l2va, the alignment line MUST use S.SS = {float(duration):.2f}."
        )
    lines.extend(["", "Scene note:", scene.strip()])
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


def _elaborate_user(expanded: str, inventory: str | None) -> str:
    """构造补细节 USER：扩写稿 + 可选库存。"""
    lines = ["Expand the scene note below to official Context-IR detail level."]
    if inventory:
        lines.extend(["", "Reference inventory:", inventory.strip()])
    lines.extend(["", "Scene note:", expanded.strip()])
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
    enable_verify: bool = True,
    verify_intent_llm: bool | None = None,
) -> dict[str, Any]:
    """
    跑完感知（若需要）→ 风格 skill 路由 → 扩写 → 注入官方指南后格式化。

    skills: 强制加载的风格 skill id。
    skill_router: off / keyword / hybrid / llm。hybrid 时关键词未命中才问前置模型。

    Returns:
        含 prompt、各步原文、mode
    """
    mode = mode.lower().strip()
    if mode not in ALL_MODES:
        raise ValueError(f"mode 须为 {' / '.join(ALL_MODES)}")
    intent = (intent or "").strip()
    if not intent:
        raise ValueError("短意图为空")

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

    router_mode = (skill_router or "hybrid").strip().lower()
    if router_mode not in ROUTER_MODES:
        raise ValueError(f"skill_router 须为 {' / '.join(ROUTER_MODES)}")
    style_sel = select_style_skills(
        intent,
        inventory=inventory,
        forced=skills,
        router=router_mode,
        classify=chat if router_mode in {"hybrid", "llm"} else None,
    )
    style_block = style_block_for_user(style_sel)
    extra_guides = style_sel.overlay_pairs()
    if style_sel.ids:
        steps.append(
            {
                "stage": "skill_route",
                "text": f"source={style_sel.source}; skills={', '.join(style_sel.ids)}",
            }
        )

    expand_sys = load_prompt("expand_intent")
    expanded = chat(
        expand_sys,
        _expand_user(intent, inventory=inventory, mode=mode, style_block=style_block),
        stage="expand",
    )
    steps.append({"stage": "expand", "text": expanded})

    # 补细节：把扩写稿提升到官方 Context-IR 的详略级别（散文，未进字段）。
    elaborate_sys = load_prompt("elaborate")
    elaborated = chat(
        elaborate_sys,
        _elaborate_user(expanded, inventory),
        stage="elaborate",
    )
    steps.append({"stage": "elaborate", "text": elaborated})

    format_sys = compose_format_system(mode, load_prompt("format_h3"), extra_guides)
    # 格式化直接消费补细节稿（已含构图/镜头/声音/音乐细节），保证成果进入最终提示词。
    format_text = _format_user(mode, elaborated, inventory=inventory, duration=dur)
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
    )
    if verify_result["prompt"] != prompt:
        prompt = verify_result["prompt"]
        steps.append({"stage": "verify_fix", "text": prompt})

    record: dict[str, Any] = {
        "mode": mode,
        "intent": intent,
        "duration": dur,
        "first_frame": first_frame,
        "last_frame": last_frame,
        "reference_images": images if mode == "r2va" else [],
        "i2va_first_frame": first_frame if mode == "i2va" else None,
        "reference_videos": videos,
        "reference_audios": audios,
        "inventory": inventory,
        "expanded": expanded,
        "elaborated": elaborated,
        "style_skills": style_sel.ids,
        "style_skill_source": style_sel.source,
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
        slim = {k: v for k, v in record.items() if k != "steps"}
        slim["steps"] = [{"stage": s["stage"]} for s in steps]
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
