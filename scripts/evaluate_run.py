#!/usr/bin/env python3
"""对管线 run 目录做十八维独立评估（本地 Qwen / OpenAI 兼容接口）。

用法示例：
  # 评估单个 run
  python3 scripts/evaluate_run.py runs/t2va_20260825_120000

  # 批量评估多个 run 目录，并写汇总
  python3 scripts/evaluate_run.py runs/t2va_* --summary-out runs/eval_summary.json

  # 直接喂三件套（不经过 run.json）
  python3 scripts/evaluate_run.py --intent "一只橘猫" --prompt-file prompt.txt -o /tmp/eval_one
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.eval_dimensions import DIMENSIONS  # noqa: E402
from src.judge import (  # noqa: E402
    aggregate_eval_results,
    evaluate_package,
    evaluate_run_dir,
    write_eval_artifacts,
)


def _expand_run_dirs(paths: list[str]) -> list[Path]:
    """把命令行路径展开为含 run.json 或 prompt.txt 的目录列表。"""
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_file() and p.name == "run.json":
            p = p.parent
        if not p.exists():
            # 支持未展开的 glob
            matches = sorted(Path().glob(raw)) or sorted(ROOT.glob(raw))
            for m in matches:
                if m.is_dir():
                    out.append(m.resolve())
                elif m.is_file() and m.name == "run.json":
                    out.append(m.parent.resolve())
            continue
        if p.is_dir():
            out.append(p.resolve())
    # 去重保序
    seen: set[str] = set()
    uniq: list[Path] = []
    for d in out:
        key = str(d)
        if key not in seen:
            seen.add(key)
            uniq.append(d)
    return uniq


def _render_summary_md(summary: dict, results: list[dict]) -> str:
    """渲染批量汇总 Markdown。"""
    lines = [
        "# 十八维评估汇总",
        "",
        f"- cases: **{summary.get('n_cases')}**",
        f"- overall_mean: **{summary.get('overall_mean')}**",
        "",
        "## 各维均分",
        "",
        "| 维度 | 均分 |",
        "| --- | --- |",
    ]
    means = summary.get("dimension_means") or {}
    for dim in DIMENSIONS:
        val = means.get(dim["id"])
        cell = "—" if val is None else f"{val:.2f}"
        lines.append(f"| {dim['name']} | {cell} |")
    tags = summary.get("issue_tag_counts") or {}
    if tags:
        lines.extend(["", "## 常见问题标签", ""])
        for tag, n in tags.items():
            lines.append(f"- `{tag}` × {n}")
    lines.extend(["", "## 各 case overall", ""])
    for res in results:
        pkg = res.get("package") or {}
        intent = str(pkg.get("intent") or "")[:40]
        lines.append(f"- overall={res.get('overall')} | mode={pkg.get('mode')} | {intent}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    """解析参数并执行评估。"""
    p = argparse.ArgumentParser(description="十八维本地裁判：评估 ir_agent 管线输出")
    p.add_argument(
        "run_dirs",
        nargs="*",
        help="runs 下的运行目录（含 run.json / prompt.txt）",
    )
    p.add_argument("--intent", default="", help="直接评估时的短意图")
    p.add_argument("--prompt-file", default="", help="直接评估时的最终提示词文件")
    p.add_argument("--inventory-file", default="", help="直接评估时的 Gemini 库存文件（可选）")
    p.add_argument("--mode", default="t2va", help="直接评估时的模式标记")
    p.add_argument("-o", "--out-dir", default="", help="直接评估时的输出目录")
    p.add_argument(
        "--summary-out",
        default="",
        help="批量评估时写入汇总 JSON 路径（同目录写 .md）",
    )
    args = p.parse_args()

    # 路径 A：直接三件套
    if args.prompt_file:
        prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
        inventory = ""
        if args.inventory_file:
            inventory = Path(args.inventory_file).expanduser().read_text(encoding="utf-8")
        intent = (args.intent or "").strip()
        if not intent:
            print("直接评估需要 --intent", file=sys.stderr)
            return 2
        package = {
            "mode": args.mode,
            "intent": intent,
            "inventory": inventory,
            "prompt": prompt,
            "style_skills": [],
            "style_skill_scores": {},
        }
        result = evaluate_package(package)
        out_dir = Path(args.out_dir).expanduser() if args.out_dir else Path.cwd() / "eval_out"
        write_eval_artifacts(out_dir, result)
        print(f"overall={result.get('overall')} → {out_dir}/eval.json")
        return 0

    run_dirs = _expand_run_dirs(args.run_dirs)
    if not run_dirs:
        print("请指定至少一个 run 目录，或使用 --prompt-file + --intent", file=sys.stderr)
        return 2

    results: list[dict] = []
    for d in run_dirs:
        print(f"评估 {d} ...")
        try:
            res = evaluate_run_dir(d)
        except Exception as exc:  # noqa: BLE001
            print(f"  失败: {exc}", file=sys.stderr)
            continue
        results.append(res)
        print(f"  overall={res.get('overall')} → {d}/eval.json")

    if not results:
        return 1

    if len(results) > 1 or args.summary_out:
        summary = aggregate_eval_results(results)
        summary_path = (
            Path(args.summary_out).expanduser()
            if args.summary_out
            else (run_dirs[0].parent / "eval_summary.json")
        )
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        md_path = summary_path.with_suffix(".md")
        md_path.write_text(_render_summary_md(summary, results), encoding="utf-8")
        print(f"汇总 → {summary_path} / {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
