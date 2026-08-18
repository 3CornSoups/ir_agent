"""T2VA / I2VA / R2VA 多步 Gemini 编排；最后一步共用 format_h3。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ROOT, h3_settings, load_prompt
from .gemini import chat
from .media import user_parts
from .video import generate_video

CANVAS_RE = re.compile(
    r"(?:,\s*)?(?:\d+:\d+\s*)?(?:aspect ratio|canvas size|resolution|帧率|画幅)[^.;，。]*|"
    r"(?:16:9|9:16|21:9|4:3|3:4|1:1)\s*(?:aspect ratio|横屏|竖屏)?|"
    r"\b(?:768P|2K|1280x720|1920x1080|\d+\s*fps)\b",
    re.I,
)
DURATION_RE = re.compile(r"(?:约|大概)?\s*(\d{1,2})\s*秒")


def infer_duration(intent: str, fallback: int = 5) -> int:
    """从短意图里的「约 N 秒」推断时长；夹到 4–15。"""
    m = DURATION_RE.search(intent or "")
    n = int(m.group(1)) if m else fallback
    return max(4, min(15, n))


def strip_canvas(text: str) -> str:
    """去掉误写入字段的画幅/分辨率/帧率。"""
    cleaned = CANVAS_RE.sub(" ", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip() + "\n"


def _expand_user(
    intent: str,
    *,
    inventory: str | None = None,
    mode: str,
) -> str:
    """构造扩写 USER。不写画幅、不写 API 时长。"""
    lines = [
        f"Mode: {mode}. Expand the short intent. Do not output MiniMax fields yet.",
        "",
        "Short intent:",
        intent.strip(),
    ]
    if inventory:
        lines.extend(["", "Reference inventory:", inventory.strip()])
    return "\n".join(lines)


def _format_user(
    mode: str,
    expanded: str,
    *,
    inventory: str | None,
    duration: int | None,
) -> str:
    """构造共用格式化 USER：模式 + 扩写稿 + 可选库存。"""
    lines = [
        f"MODE={mode}",
        "Serialize the expanded scene into the MiniMax-H3 fields for this MODE.",
        "Do not mention aspect ratio, resolution, fps, or canvas size.",
    ]
    if duration is not None:
        lines.append(
            f"Cut-time hint only: if you write timestamps, keep them within {duration:g} seconds. "
            "Do not write the duration into the prompt text."
        )
    lines.extend(["", "Expanded scene:", expanded.strip()])
    if inventory:
        lines.extend(["", "Reference inventory:", inventory.strip()])
    return "\n".join(lines)


def enhance(
    mode: str,
    intent: str,
    *,
    first_frame: str | None = None,
    reference_images: list[str] | None = None,
    reference_videos: list[str] | None = None,
    reference_audios: list[str] | None = None,
    duration: int | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """
    跑完感知（若需要）→ 扩写 → 共用格式化。

    Returns:
        含 prompt、各步原文、mode
    """
    mode = mode.lower().strip()
    if mode not in {"t2va", "i2va", "r2va"}:
        raise ValueError("mode 须为 t2va / i2va / r2va")
    intent = (intent or "").strip()
    if not intent:
        raise ValueError("短意图为空")

    images = list(reference_images or [])
    videos = list(reference_videos or [])
    audios = list(reference_audios or [])
    if mode == "i2va":
        if not first_frame:
            raise ValueError("i2va 需要 --first-frame")
        images = [first_frame] + images

    steps: list[dict[str, str]] = []
    inventory: str | None = None

    if mode == "i2va":
        system = load_prompt("perceive_image")
        user = user_parts(
            "Describe this first-frame image for I2VA.",
            images=[first_frame] if first_frame else None,
        )
        inventory = chat(system, user, stage="perceive")
        steps.append({"stage": "perceive_image", "text": inventory})
    elif mode == "r2va":
        if not images and not videos:
            raise ValueError("r2va 须至少 1 张参考图或 1 段参考视频")
        labels = []
        for i, p in enumerate(images, 1):
            labels.append(f"<Picture {i}> = {p}")
        for i, p in enumerate(videos, 1):
            labels.append(f"<Video {i}> = {p}")
        for i, p in enumerate(audios, 1):
            labels.append(f"<Audio {i}> = {p}")
        system = load_prompt("perceive_refs")
        user = user_parts(
            "Inventory attached assets in this order:\n" + "\n".join(labels),
            images=images or None,
            videos=videos or None,
            audios=audios or None,
        )
        inventory = chat(system, user, stage="perceive")
        steps.append({"stage": "perceive_refs", "text": inventory})

    expand_sys = load_prompt("expand_intent")
    expanded = chat(
        expand_sys,
        _expand_user(intent, inventory=inventory, mode=mode),
        stage="expand",
    )
    steps.append({"stage": "expand", "text": expanded})

    format_sys = load_prompt("format_h3")
    dur = duration if duration is not None else infer_duration(intent)
    raw_prompt = chat(
        format_sys,
        _format_user(mode, expanded, inventory=inventory, duration=dur),
        stage="format",
    )
    prompt = strip_canvas(raw_prompt)
    steps.append({"stage": "format", "text": prompt})

    record: dict[str, Any] = {
        "mode": mode,
        "intent": intent,
        "duration": dur,
        "first_frame": first_frame,
        "reference_images": images if mode != "i2va" else [],
        "i2va_first_frame": first_frame,
        "reference_videos": videos,
        "reference_audios": audios,
        "inventory": inventory,
        "expanded": expanded,
        "prompt": prompt,
        "steps": steps,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        (out_dir / "expanded.txt").write_text(expanded.strip() + "\n", encoding="utf-8")
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
    reference_images: list[str] | None = None,
    reference_videos: list[str] | None = None,
    reference_audios: list[str] | None = None,
    duration: int | None = None,
    ratio: str | None = None,
    resolution: str | None = None,
    out_dir: Path | None = None,
    make_video: bool = True,
    wait_video: bool = True,
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
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
        duration=dur,
        out_dir=out_dir,
    )
    rec["ratio_api"] = ratio or (h3["default_ratio"] if mode == "t2va" else "adaptive")
    rec["resolution_api"] = resolution or h3["default_resolution"]
    rec["make_video"] = make_video
    if make_video:
        video_path = Path(out_dir) / "out.mp4"
        video = generate_video(
            mode,
            rec["prompt"],
            duration=dur,
            ratio=ratio,
            resolution=resolution,
            first_frame=first_frame,
            reference_images=reference_images,
            reference_videos=reference_videos,
            reference_audios=reference_audios,
            output=video_path,
            wait=wait_video,
        )
        rec["video"] = {k: v for k, v in video.items() if k != "task"}
        rec["video"]["task_status"] = (video.get("task") or {}).get("status")
        run_path = Path(out_dir) / "run.json"
        if run_path.is_file():
            dumped = json.loads(run_path.read_text(encoding="utf-8"))
            dumped["video"] = rec["video"]
            dumped["ratio_api"] = rec["ratio_api"]
            dumped["resolution_api"] = rec["resolution_api"]
            run_path.write_text(json.dumps(dumped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rec
