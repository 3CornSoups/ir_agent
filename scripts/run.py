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
    p.add_argument("--duration", type=int, default=None, help="出片秒数 4–15；省略则从意图推断")
    p.add_argument("--ratio", default=None, help="出片画幅，只走视频 API；t2va 默认 16:9")
    p.add_argument("--resolution", default=None, choices=("768P", "2K"), help="出片分辨率")
    p.add_argument("--out-dir", type=Path, help="输出目录")
    p.add_argument("--no-video", action="store_true", help="只写 prompt，不出片")
    p.add_argument("--no-wait", action="store_true", help="出片只提交不轮询")
    p.add_argument("--compare-video", action="store_true", help="本地/官方 prompt 各出一次并做对比")
    args = p.parse_args()

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
    )
    print(f"[{rec['mode']}] prompt → {Path(rec['out_dir']) / 'prompt.txt'}")
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
