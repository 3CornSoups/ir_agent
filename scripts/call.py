#!/usr/bin/env python3
"""日常调用入口：短意图 → 增强 prompt（默认不出片）。

用法（在仓库根目录执行）：

  # 纯文字 t2va（最常用）
  python3 scripts/call.py "一只橘猫在窗台晒太阳，镜头缓推，约5秒"

  # 指定模式
  python3 scripts/call.py -m i2va "人物向前走" --first-frame path/to/first.png

  # 首尾帧
  python3 scripts/call.py -m fl2va "从站立走到门口" \\
    --first-frame first.png --last-frame last.png --duration 6

  # 多参考 r2va
  python3 scripts/call.py -m r2va "保持人设在雨夜走路" \\
    --ref-image char.png --ref-image style.png

  # 要出片时加 --video（需配置 H3 / MiniMax）
  python3 scripts/call.py "产品旋转展示，约5秒" --video --ratio 16:9

  # 强制风格 / 机制
  python3 scripts/call.py "品牌宣传片结尾 CTA" --skill brand-promo --skill-router off
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.pipeline import run_job  # noqa: E402
from src.skill import ALL_MODES  # noqa: E402


def _stamp_dir(mode: str) -> Path:
    """生成 runs/<mode>_<时间戳> 输出目录。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / "runs" / f"{mode}_{stamp}"


def main() -> int:
    """解析参数并跑一次增强（可选出片）。"""
    p = argparse.ArgumentParser(
        description="调用 ir_agent：短意图 → MiniMax-H3 提示词（默认只增强不出片）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "intent",
        nargs="?",
        default="",
        help="短意图（也可改用 --intent / --intent-file）",
    )
    p.add_argument("-m", "--mode", default="t2va", choices=ALL_MODES, help="默认 t2va")
    p.add_argument("--intent", dest="intent_flag", default="", help="短意图（与位置参数二选一）")
    p.add_argument("--intent-file", type=Path, help="从文件读短意图")
    p.add_argument("--first-frame", help="i2va / fl2va 首帧")
    p.add_argument("--last-frame", help="fl2va / l2va 尾帧")
    p.add_argument("--ref-image", action="append", default=[], help="r2va 参考图，可重复")
    p.add_argument("--ref-video", action="append", default=[], help="r2va 参考视频，可重复")
    p.add_argument("--ref-audio", action="append", default=[], help="r2va 参考音频，可重复")
    p.add_argument("--duration", type=int, default=None, help="秒数 4–15；省略则从意图推断")
    p.add_argument("--ratio", default=None, help="出片画幅，仅 --video 时生效")
    p.add_argument("--resolution", default=None, choices=("768P", "2K"), help="出片分辨率")
    p.add_argument("--out-dir", type=Path, help="输出目录；默认 runs/<mode>_<时间戳>")
    p.add_argument("--video", action="store_true", help="增强后调用 H3 出片")
    p.add_argument("--no-verify", action="store_true", help="关闭质量校验")
    p.add_argument(
        "--skill",
        action="append",
        default=[],
        dest="skills",
        help="强制风格 skill id，可重复",
    )
    p.add_argument(
        "--skill-router",
        default="hybrid",
        choices=("off", "keyword", "hybrid", "llm"),
        help="风格路由，默认 hybrid",
    )
    p.add_argument(
        "--mechanism",
        action="append",
        default=[],
        dest="mechanisms",
        help="强制 T8 机制 id，可重复",
    )
    p.add_argument(
        "--mechanism-router",
        default="hybrid",
        choices=("off", "keyword", "hybrid", "llm"),
        help="机制路由，默认 hybrid",
    )
    args = p.parse_args()

    intent = (args.intent_flag or args.intent or "").strip()
    if args.intent_file:
        intent = args.intent_file.read_text(encoding="utf-8").strip()
    if not intent:
        p.error("请提供意图：位置参数 / --intent / --intent-file")

    out_dir = args.out_dir or _stamp_dir(args.mode)
    print(f"[call] mode={args.mode}  out={out_dir}")
    print(f"[call] intent={intent[:120]}{'…' if len(intent) > 120 else ''}")

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
        out_dir=out_dir,
        make_video=bool(args.video),
        wait_video=True,
        skills=args.skills or None,
        skill_router=args.skill_router,
        mechanisms=args.mechanisms or None,
        mechanism_router=args.mechanism_router,
        enable_verify=not args.no_verify,
    )

    prompt_path = Path(rec["out_dir"]) / "prompt.txt"
    print(f"[call] prompt → {prompt_path}")
    if rec.get("style_skills"):
        print(
            f"[call] skills → {', '.join(rec['style_skills'])} "
            f"({rec.get('style_skill_source')})"
        )
    if rec.get("mechanisms"):
        print(
            f"[call] mechanisms → {', '.join(rec['mechanisms'])} "
            f"({rec.get('mechanism_source')})"
        )
    verify = rec.get("verify") or {}
    if verify:
        print(
            f"[call] verify → {verify.get('status', '?')} "
            f"(errors={verify.get('errors', 0)}, "
            f"warnings={verify.get('warnings', 0)}, "
            f"fixed={bool(verify.get('fixed'))})"
        )
    video = rec.get("video") or {}
    if video.get("video_path"):
        print(f"[call] video → {video['video_path']}")
    elif video.get("task_id"):
        print(f"[call] task_id={video['task_id']}")

    # 终端预览前几行，方便快速瞄一眼
    if prompt_path.is_file():
        preview = prompt_path.read_text(encoding="utf-8").strip().splitlines()[:12]
        print("[call] preview:")
        for line in preview:
            print(f"  {line}")
        if len(prompt_path.read_text(encoding="utf-8").strip().splitlines()) > 12:
            print("  …")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
