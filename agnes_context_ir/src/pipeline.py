"""T2VA / I2VA / FL2VA / L2VA / R2VA 精简多轮 Gemini 编排（对齐 latest_agent）。

外部接口（enhance / run_job 签名）保持兼容；skills / 机制路由参数接受但忽略。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ALL_MODES, KEYFRAME_MODES, ROOT, gemini_settings, h3_settings
from .gemini import chat
from .media import user_parts
from .postprocess import clean_final_prompt, strip_canvas, verify_local
from .prompts import (
    _asset_label_display,
    compose_format_system,
    compose_plan_system,
    r1_image_user,
    r1_keyinfo_user,
    r1_r2va_user,
    r2_format_user,
    r2_plot_user,
    r3_format_r2va_user,
)
from .video import generate_video

# 兼容旧测试 / 调用方从 pipeline 导入 strip_canvas
__all__ = [
    "enhance",
    "run_job",
    "infer_duration",
    "strip_canvas",
]


_CN_NUM = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
}


def infer_duration(intent: str, fallback: int = 5) -> int:
    """从短意图推断时长（中/英秒、中文数字、分钟）；夹到 4–15。"""
    text = intent or ""
    m = re.search(
        r"(?:约|大概)?\s*(\d{1,2})\s*(?:秒|s\b|sec(?:onds?)?)\b",
        text,
        re.I,
    )
    if not m:
        m = re.search(
            r"\b(\d{1,2})\s*[- ]?(?:second|sec)s?\b",
            text,
            re.I,
        )
    if m:
        return max(4, min(15, int(m.group(1))))
    m = re.search(r"(?:约|大概)?\s*(\d{1,2})\s*分钟", text)
    if m:
        return max(4, min(15, int(m.group(1)) * 60))
    m = re.search(r"(?:约|大概)?\s*([一二两三四五六七八九十两〇零]{1,3})\s*秒", text)
    if m:
        token = m.group(1)
        if token in _CN_NUM:
            return max(4, min(15, _CN_NUM[token]))
        if token.startswith("十") and len(token) == 2:
            return max(4, min(15, 10 + _CN_NUM.get(token[1], 0)))
    return max(4, min(15, int(fallback)))


def _text_model_name() -> str | None:
    """返回 t2va / 纯文本轮应使用的模型名；未配置则 None（走默认多模态模型）。"""
    name = (gemini_settings().get("text_model") or "").strip()
    return name or None


def _keyframe_images(
    mode: str,
    *,
    first_frame: str | None,
    last_frame: str | None,
) -> list[str]:
    """返回关键帧模式需要上传的图片路径列表。"""
    if mode == "i2va":
        return [first_frame] if first_frame else []
    if mode == "fl2va":
        return [p for p in (first_frame, last_frame) if p]
    if mode == "l2va":
        return [last_frame] if last_frame else []
    return []


def _asset_labels(
    *,
    images: list[str],
    videos: list[str],
    audios: list[str],
) -> list[str]:
    """生成 r2va 素材短标签（不含 data URI / 绝对路径）。"""
    labels: list[str] = []
    for i, p in enumerate(images, 1):
        labels.append(_asset_label_display(p, "image", i))
    for i, p in enumerate(videos, 1):
        labels.append(_asset_label_display(p, "video", i))
    for i, p in enumerate(audios, 1):
        labels.append(_asset_label_display(p, "audio", i))
    return labels


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
    按模式跑 2~3 轮 Gemini，输出 MiniMax-H3 结构化提示词。

    skills / skill_router / mechanisms / mechanism_router：保留参数以兼容产线调用，运行时忽略。
    enable_verify / verify_intent_llm：保留兼容；瘦身后仅做本地清洗，不再打 LLM 修复轮。
    """
    del skills, skill_router, mechanisms, mechanism_router, verify_intent_llm
    _ = enable_verify  # 兼容占位：本地清洗始终执行

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
        if not images and not videos and not audios:
            raise ValueError(
                "r2va 须至少 1 张参考图、1 段参考视频或 1 段参考音频"
            )
        if len(images) > 9:
            raise ValueError("r2va 参考图数量 ≤ 9")
        if len(videos) > 3:
            raise ValueError("r2va 参考视频数量 ≤ 3")
        if len(audios) > 3:
            raise ValueError("r2va 参考音频数量 ≤ 3")

    dur = duration if duration is not None else infer_duration(intent)
    dur = max(4, min(15, int(dur)))
    steps: list[dict[str, Any]] = []
    rounds: list[dict[str, Any]] = []
    text_model = _text_model_name()
    inventory: str | None = None
    expanded = ""
    elaborated = ""

    if mode == "t2va":
        r1_sys = compose_plan_system("r1_keyinfo", mode)
        r1_user = r1_keyinfo_user(intent, mode=mode, duration=dur)
        r1_out = chat(r1_sys, r1_user, stage="expand", model=text_model)
        steps.append({"stage": "r1_keyinfo", "text": r1_out})
        rounds.append({"round": 1, "stage": "r1_keyinfo", "output": r1_out})
        expanded = r1_out

        r2_sys = compose_format_system("r2_format", mode)
        r2_user = r2_format_user(intent, mode=mode, duration=dur, upstream=r1_out)
        r2_out = chat(r2_sys, r2_user, stage="format", model=text_model)
        steps.append({"stage": "r2_format", "text": r2_out})
        rounds.append({"round": 2, "stage": "r2_format", "output": r2_out})
        elaborated = r2_out
        official_prompt = r2_out
        prompt = clean_final_prompt(r2_out, mode, dur)
        upstream_for_verify = r1_out

    elif mode in KEYFRAME_MODES:
        r1_sys = compose_plan_system("r1_image_guide", mode)
        r1_user = r1_image_user(intent, mode=mode, duration=dur)
        kf_images = _keyframe_images(
            mode, first_frame=first_frame, last_frame=last_frame
        )
        r1_out = chat(
            r1_sys,
            user_parts(r1_user, images=kf_images or None),
            stage="perceive",
        )
        steps.append({"stage": "r1_image_guide", "text": r1_out})
        rounds.append({"round": 1, "stage": "r1_image_guide", "output": r1_out})
        inventory = r1_out
        expanded = r1_out

        r2_sys = compose_format_system("r2_format", mode)
        r2_user = r2_format_user(intent, mode=mode, duration=dur, upstream=r1_out)
        r2_out = chat(r2_sys, r2_user, stage="format", model=text_model)
        steps.append({"stage": "r2_format", "text": r2_out})
        rounds.append({"round": 2, "stage": "r2_format", "output": r2_out})
        elaborated = r2_out
        official_prompt = r2_out
        prompt = clean_final_prompt(r2_out, mode, dur)
        upstream_for_verify = r1_out

    elif mode == "r2va":
        labels = _asset_labels(images=images, videos=videos, audios=audios)
        r1_sys = compose_plan_system("r1_perceive_fuse", mode, stage="r1_perceive_fuse")
        r1_user = r1_r2va_user(intent, labels=labels, duration=dur)
        r1_out = chat(
            r1_sys,
            user_parts(
                r1_user,
                images=images or None,
                videos=videos or None,
                audios=audios or None,
            ),
            stage="perceive",
        )
        steps.append({"stage": "r1_perceive_fuse", "text": r1_out})
        rounds.append({"round": 1, "stage": "r1_perceive_fuse", "output": r1_out})
        inventory = r1_out

        r2_sys = compose_plan_system("r2_plot", mode, stage="r2_plot")
        r2_user = r2_plot_user(intent, r1_out, duration=dur)
        r2_out = chat(r2_sys, r2_user, stage="expand", model=text_model)
        steps.append({"stage": "r2_plot", "text": r2_out})
        rounds.append({"round": 2, "stage": "r2_plot", "output": r2_out})
        expanded = r2_out

        r3_sys = compose_format_system("r3_format_r2va", mode)
        r3_user = r3_format_r2va_user(intent, r1_out, r2_out, duration=dur)
        r3_out = chat(r3_sys, r3_user, stage="format", model=text_model)
        steps.append({"stage": "r3_format_r2va", "text": r3_out})
        rounds.append({"round": 3, "stage": "r3_format_r2va", "output": r3_out})
        elaborated = r3_out
        official_prompt = r3_out
        prompt = clean_final_prompt(r3_out, mode, dur)
        upstream_for_verify = f"{r1_out}\n\n{r2_out}"

    else:
        raise ValueError(f"未实现模式: {mode}")

    verify_result = verify_local(
        prompt,
        mode=mode,
        duration=dur,
        upstream=upstream_for_verify,
    )
    prompt = verify_result.get("prompt") or prompt

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
        "contract": {},
        "expanded": expanded,
        "elaborated": elaborated,
        "style_skills": [],
        "style_skill_source": "off",
        "style_skill_scores": {},
        "style_skill_threshold": None,
        "mechanisms": [],
        "mechanism_source": "off",
        "prompt_official": official_prompt,
        "prompt": prompt,
        "prompt_raw": official_prompt,
        "verify": verify_result,
        "steps": steps,
        "rounds": rounds,
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
        if expanded:
            (out_dir / "expanded.txt").write_text(
                expanded.strip() + "\n", encoding="utf-8"
            )
        if elaborated:
            (out_dir / "elaborated.txt").write_text(
                elaborated.strip() + "\n", encoding="utf-8"
            )
        if inventory:
            (out_dir / "inventory.txt").write_text(
                inventory.strip() + "\n", encoding="utf-8"
            )
        for rnd in rounds:
            n = rnd.get("round")
            stage = rnd.get("stage", "unknown")
            text = rnd.get("output") or ""
            if n is not None:
                (out_dir / f"round_{n}.txt").write_text(
                    text.strip() + "\n", encoding="utf-8"
                )
            (out_dir / f"{stage}.txt").write_text(text.strip() + "\n", encoding="utf-8")
        (out_dir / "run.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
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
            video_official = {
                k: v for k, v in video_official_res.items() if k != "task"
            }
            video_official["task_status"] = (
                video_official_res.get("task") or {}
            ).get("status")
            rec["video_official"] = video_official

        run_path = Path(out_dir) / "run.json"
        if run_path.is_file():
            dumped = json.loads(run_path.read_text(encoding="utf-8"))
            dumped["video"] = rec.get("video")
            dumped["video_official"] = rec.get("video_official")
            dumped["ratio_api"] = rec["ratio_api"]
            dumped["resolution_api"] = rec["resolution_api"]
            run_path.write_text(
                json.dumps(dumped, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    from .report import write_report

    write_report(
        out_dir,
        record=rec,
        prompt_official=prompt_official,
        prompt_local=prompt_local,
        video_official=video_official,
        video_local=video_local,
    )
    return rec
