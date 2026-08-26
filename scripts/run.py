#!/usr/bin/env python3
"""五模式 Agent：Gemini 扩写 + 官方 skill 格式化 + 可选 H3 出片。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.pipeline import run_job  # noqa: E402
from src.skill import ALL_MODES  # noqa: E402
from src.video import ALL_RATIOS  # noqa: E402


def main() -> int:
    """解析 CLI 并跑一条任务。"""
    p = argparse.ArgumentParser(description="Gemini → MiniMax-H3 五模式 Agent（官方 skill 对齐）")
    p.add_argument("-m", "--mode", required=True, choices=ALL_MODES)
    p.add_argument("--intent", default="", help="短意图文本")
    p.add_argument("--intent-file", type=Path, help="从文件读短意图")
    p.add_argument("--first-frame", help="i2va / fl2va 首帧图")
    p.add_argument("--last-frame", help="fl2va / l2va 尾帧图")
    p.add_argument("--ref-image", action="append", default=[], help="r2va 参考图，可重复")
    p.add_argument("--ref-video", action="append", default=[], help="r2va 参考视频，可重复")
    p.add_argument("--ref-audio", action="append", default=[], help="r2va 参考音频，可重复")
    p.add_argument(
        "--skill",
        action="append",
        default=[],
        dest="skills",
        help="强制加载风格 skill id（如 brand-promo），可重复",
    )
    p.add_argument(
        "--skill-router",
        default="hybrid",
        choices=("off", "keyword", "hybrid", "llm"),
        help="风格 skill 路由：off / hybrid|llm（量化打分取 top1）/ keyword（=llm 别名）",
    )
    p.add_argument(
        "--mechanism",
        action="append",
        default=[],
        dest="mechanisms",
        help="强制加载 T8 Creative DNA 机制 id，可重复",
    )
    p.add_argument(
        "--mechanism-router",
        default="hybrid",
        choices=("off", "keyword", "hybrid", "llm"),
        help="T8 机制路由：off / keyword / hybrid（默认） / llm",
    )
    p.add_argument(
        "--duration",
        type=int,
        required=True,
        help="出片/增强时长（秒），必填，范围 4–15",
    )
    p.add_argument(
        "--ratio",
        required=True,
        help="画幅，必填，只走视频 API（如 16:9；非 t2va 可用 adaptive）",
    )
    p.add_argument("--resolution", default=None, choices=("768P", "2K"), help="出片分辨率")
    p.add_argument("--out-dir", type=Path, help="输出目录")
    p.add_argument("--no-video", action="store_true", help="只写 prompt，不出片")
    p.add_argument("--no-wait", action="store_true", help="出片只提交不轮询")
    p.add_argument("--compare-video", action="store_true", help="本地/官方 prompt 各出一次并做对比")
    p.add_argument("--no-verify", action="store_true", help="关闭提示词质量校验（含规则与自动修复）")
    p.add_argument(
        "--verify-intent-llm",
        action="store_true",
        default=None,
        help="开启 LLM 意图一致性检查（对比原始意图与最终提示词，+1 次调用）",
    )
    args = p.parse_args()

    if args.duration < 4 or args.duration > 15:
        p.error("--duration 须在 4–15 秒")
    if args.ratio not in ALL_RATIOS:
        p.error(f"--ratio 非法，可选: {', '.join(ALL_RATIOS)}")

    intent = args.intent.strip()
    if args.intent_file:
        intent = args.intent_file.read_text(encoding="utf-8")
    if not intent.strip():
        p.error("请提供 --intent 或 --intent-file")

    rec = run_job(
        args.mode,
        intent,
        first_frame=args.first_frame,
        last_frame=args.last_frame,
        reference_images=args.ref_image or None,
        reference_videos=args.ref_video or None,
        reference_audios=args.ref_audio or None,
        duration=args.duration,
        ratio=args.ratio,
        resolution=args.resolution,
        out_dir=args.out_dir,
        make_video=not args.no_video,
        wait_video=not args.no_wait,
        compare_video=args.compare_video,
        skills=args.skills or None,
        skill_router=args.skill_router,
        mechanisms=args.mechanisms or None,
        mechanism_router=args.mechanism_router,
        enable_verify=not args.no_verify,
        verify_intent_llm=args.verify_intent_llm,
    )
    print(f"[{rec['mode']}] prompt → {Path(rec['out_dir']) / 'prompt.txt'}")
    if rec.get("style_skills"):
        print(f"[{rec['mode']}] skills → {', '.join(rec['style_skills'])} ({rec.get('style_skill_source')})")
    if rec.get("mechanisms"):
        print(
            f"[{rec['mode']}] mechanisms → {', '.join(rec['mechanisms'])} "
            f"({rec.get('mechanism_source')})"
        )
    verify = rec.get("verify") or {}
    if verify:
        status = verify.get("status", "?")
        print(
            f"[{rec['mode']}] verify → {status} "
            f"(errors={verify.get('errors', 0)}, warnings={verify.get('warnings', 0)}, "
            f"fixed={bool(verify.get('fixed'))})"
        )
    if rec.get("video", {}).get("video_path"):
        print(f"[{rec['mode']}] video → {rec['video']['video_path']}")
    elif rec.get("video", {}).get("task_id"):
        print(f"[{rec['mode']}] task_id={rec['video']['task_id']}")

    if args.compare_video and rec.get("video_official"):
        if rec["video_official"].get("video_path"):
            print(f"[{rec['mode']}] video_official → {rec['video_official']['video_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
