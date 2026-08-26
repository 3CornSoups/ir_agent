#!/usr/bin/env python3
"""一键运行 Agent 增强，输出增强结果与运行日志（面向服务器）。

每次运行（一条意图）生成：
  - log/run_<模式>_<时间戳>_<序号>/   模型请求/响应日志（按 stage 编号成对）
  - runs/<模式>_<时间戳>_<序号>/       增强结果（prompt.txt / expanded / elaborated / run.json 等）

用法示例：
  # 单条意图，只增强不出片
  python3 scripts/oneclick_run.py -m t2va --intent "一只橘猫在窗台晒太阳"

  # 从文件读意图
  python3 scripts/oneclick_run.py -m t2va --intent-file input.txt

  # 批量（每行一条意图）
  python3 scripts/oneclick_run.py -m t2va --intents-file intents.txt

  # 出片（需要配置 configs/h3.yaml 或 MINIMAX_API_KEY）
  python3 scripts/oneclick_run.py -m i2va --intent "人物向前走" \
    --first-frame first.png --duration 5 --video

  # 只看增强结果，不自动质量校验
  python3 scripts/oneclick_run.py -m t2va --intent "..." --no-verify
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.pipeline import run_job  # noqa: E402
from src.runlog import LOG_ROOT, activate, deactivate, write_meta  # noqa: E402
from src.skill import ALL_MODES  # noqa: E402


def _load_intents(path: Path) -> list[str]:
    """按行读取意图文件；空行与 # 注释跳过。"""
    lines = (path.read_text(encoding="utf-8") or "").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def main() -> int:
    """解析参数、逐条运行并打印结果与日志路径。"""
    p = argparse.ArgumentParser(
        description="一键运行 Agent 增强（输出增强结果 + 每次运行的模型调用日志）"
    )
    p.add_argument("-m", "--mode", required=True, choices=ALL_MODES)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--intent", default="", help="单条短意图")
    src.add_argument("--intent-file", type=Path, help="从文本文件读单条意图")
    src.add_argument("--intents-file", type=Path, help="批量：每行一条意图（# 注释跳过）")
    p.add_argument("--first-frame", help="i2va / fl2va 首帧图")
    p.add_argument("--last-frame", help="fl2va / l2va 尾帧图")
    p.add_argument("--ref-image", action="append", default=[], help="r2va 参考图，可重复")
    p.add_argument("--ref-video", action="append", default=[], help="r2va 参考视频，可重复")
    p.add_argument("--ref-audio", action="append", default=[], help="r2va 参考音频，可重复")
    p.add_argument("--duration", type=int, default=None, help="出片秒数 4–15；省略则从意图推断")
    p.add_argument("--ratio", default=None, help="出片画幅，只走视频 API")
    p.add_argument("--resolution", default=None, choices=("768P", "2K"), help="出片分辨率")
    p.add_argument(
        "--skill",
        action="append",
        default=[],
        dest="skills",
        help="强制加载风格 skill id，可重复",
    )
    p.add_argument(
        "--skill-router",
        default="hybrid",
        choices=("off", "keyword", "hybrid", "llm"),
        help="风格 skill 路由：off / keyword（触发词命中）/ hybrid|llm（LLM 打分取 top1）",
    )
    p.add_argument(
        "--mechanism",
        action="append",
        default=[],
        dest="mechanisms",
        help="强制加载 T8 机制 id，可重复",
    )
    p.add_argument(
        "--mechanism-router",
        default="hybrid",
        choices=("off", "keyword", "hybrid", "llm"),
        help="T8 机制路由",
    )
    p.add_argument("--video", action="store_true", help="增强后调用 H3 出片（需配置 h3.yaml）")
    p.add_argument("--no-wait", action="store_true", help="出片只提交不轮询")
    p.add_argument("--compare-video", action="store_true", help="本地/官方 prompt 各出一次做对比")
    p.add_argument("--no-verify", action="store_true", help="关闭提示词质量校验")
    p.add_argument(
        "--judge",
        action="store_true",
        help="增强后用本地裁判模型按 18 维打分（需 configs/judge.yaml）",
    )
    p.add_argument("--out-root", type=Path, default=ROOT / "runs", help="增强结果输出根目录")
    p.add_argument("--log-root", type=Path, default=LOG_ROOT, help="运行日志根目录")
    args = p.parse_args()

    if args.intent_file:
        intents = [args.intent_file.read_text(encoding="utf-8").strip()]
    elif args.intents_file:
        intents = _load_intents(args.intents_file)
        if not intents:
            print(f"[错误] 意图文件无有效内容：{args.intents_file}")
            return 1
    else:
        intents = [args.intent.strip()]
        if not intents[0]:
            p.error("--intent 不能为空")

    print(
        f"模式={args.mode}  意图数={len(intents)}  出片={args.video}  质量校验={'关' if args.no_verify else '开'}"
    )
    print(f"结果目录：{args.out_root.resolve()}  日志目录：{args.log_root.resolve()}\n")

    failed = 0
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for idx, intent in enumerate(intents, 1):
        run_id = f"run_{args.mode}_{stamp}_{idx:03d}"
        log_dir = args.log_root / run_id
        out_dir = args.out_root / run_id
        activate(log_dir)
        try:
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
                make_video=args.video,
                wait_video=not args.no_wait,
                compare_video=args.compare_video,
                skills=args.skills or None,
                skill_router=args.skill_router,
                mechanisms=args.mechanisms or None,
                mechanism_router=args.mechanism_router,
                enable_verify=not args.no_verify,
            )
            write_meta(
                {
                    "run_id": run_id,
                    "mode": args.mode,
                    "intent": intent,
                    "out_dir": str(out_dir),
                    "log_dir": str(log_dir),
                    "verify": rec.get("verify"),
                    "style_skills": rec.get("style_skills"),
                    "mechanisms": rec.get("mechanisms"),
                }
            )
            verify = rec.get("verify") or {}
            status = verify.get("status", "?")
            ok = status in ("passed", "") or rec.get("make_video") is False
            mark = "OK" if ok else "NG"
            print(f"[{idx}/{len(intents)}] [{mark}] {run_id}")
            print(f"  intent  : {intent[:60]}")
            print(f"  prompt  : {out_dir / 'prompt.txt'}")
            print(f"  verify  : {status} (errors={verify.get('errors', 0)})")
            if args.judge:
                try:
                    from src.judge import evaluate_run_dir

                    ev = evaluate_run_dir(out_dir)
                    print(f"  judge   : overall={ev.get('overall')} → {out_dir / 'eval.json'}")
                except Exception as judge_exc:  # noqa: BLE001
                    print(f"  judge   : FAILED ({judge_exc})")
                    failed += 1
            print(f"  日志    : {log_dir}\n")
            if not ok:
                failed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            write_meta(
                {
                    "run_id": run_id,
                    "mode": args.mode,
                    "intent": intent,
                    "error": str(exc),
                    "log_dir": str(log_dir),
                }
            )
            print(f"[{idx}/{len(intents)}] [NG] {run_id}: {exc}")
            print(f"  日志    : {log_dir}\n")
        finally:
            deactivate()

    print(f"完成：{len(intents) - failed}/{len(intents)} 成功")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
