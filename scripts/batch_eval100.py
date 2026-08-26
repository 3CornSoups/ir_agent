#!/usr/bin/env python3
"""批量跑 t2va 增强 + 十八维裁判，并汇总分析。

用法：
  python3 scripts/batch_eval100.py
  python3 scripts/batch_eval100.py --limit 20          # 先小批量
  python3 scripts/batch_eval100.py --skip-enhance      # 只补打分
  python3 scripts/batch_eval100.py --skip-judge        # 只增强
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.eval_dimensions import DIMENSIONS  # noqa: E402
from src.judge import aggregate_eval_results, evaluate_run_dir  # noqa: E402
from src.pipeline import enhance  # noqa: E402


def _load_cases(manifest: Path, intents_file: Path) -> list[dict]:
    """读取样例清单；无 jsonl 时退化为纯意图列表。"""
    cases: list[dict] = []
    if manifest.is_file():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            cases.append(json.loads(line))
        return cases
    for i, line in enumerate(intents_file.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if text:
            cases.append({"id": f"c{i:03d}", "focus": "mix", "intent": text})
    return cases


def _analyze(results: list[dict], cases: list[dict], out_dir: Path) -> dict:
    """按维度与 focus 标签汇总，并挑出低分 case。"""
    summary = aggregate_eval_results(results)
    by_id = {c["id"]: c for c in cases}
    focus_scores: dict[str, list[float]] = defaultdict(list)
    low_cases: list[dict] = []
    dim_lows: dict[str, list[str]] = defaultdict(list)
    for res in results:
        cid = (res.get("package") or {}).get("case_id") or ""
        focus = by_id.get(cid, {}).get("focus", "mix")
        overall = res.get("overall")
        if isinstance(overall, (int, float)):
            focus_scores[focus].append(float(overall))
            if overall < 3.5:
                low_cases.append(
                    {
                        "id": cid,
                        "focus": focus,
                        "overall": overall,
                        "intent": (res.get("package") or {}).get("intent"),
                        "weaknesses": res.get("weaknesses") or [],
                        "issue_tags": res.get("issue_tags") or [],
                        "scores": res.get("scores") or {},
                    }
                )
        scores = res.get("scores") or {}
        for dim in DIMENSIONS:
            val = scores.get(dim["id"])
            if isinstance(val, int) and val <= 2:
                dim_lows[dim["id"]].append(cid)

    focus_means = {
        k: round(sum(v) / len(v), 2) if v else None for k, v in sorted(focus_scores.items())
    }
    tag_counter: Counter[str] = Counter()
    for res in results:
        for t in res.get("issue_tags") or []:
            tag_counter[str(t)] += 1

    report = {
        "n_results": len(results),
        "overall_mean": summary.get("overall_mean"),
        "dimension_means": summary.get("dimension_means"),
        "focus_means": focus_means,
        "issue_tag_counts": dict(tag_counter.most_common()),
        "low_cases": sorted(low_cases, key=lambda x: x.get("overall") or 0),
        "dimension_low_hits": {k: v for k, v in dim_lows.items() if v},
        "weakest_dimensions": sorted(
            (
                (did, mean)
                for did, mean in (summary.get("dimension_means") or {}).items()
                if mean is not None
            ),
            key=lambda kv: kv[1],
        )[:8],
    }
    (out_dir / "analysis.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    name = {d["id"]: d["name"] for d in DIMENSIONS}
    md = [
        "# eval100 分析报告",
        "",
        f"- cases: **{report['n_results']}**",
        f"- overall_mean: **{report['overall_mean']}**",
        "",
        "## 最弱维度（均分升序）",
        "",
    ]
    for did, mean in report["weakest_dimensions"]:
        md.append(f"- {name.get(did, did)} (`{did}`): **{mean}**")
    md.extend(["", "## Focus 组均分", ""])
    for k, v in (report["focus_means"] or {}).items():
        md.append(f"- `{k}`: {v}")
    md.extend(["", "## 常见 issue_tags", ""])
    for t, n in (report["issue_tag_counts"] or {}).items():
        md.append(f"- `{t}` × {n}")
    md.extend(["", "## 低分 case（overall < 3.5）", ""])
    for item in report["low_cases"][:30]:
        md.append(
            f"- `{item['id']}` overall={item['overall']} focus={item['focus']}: "
            f"{(item.get('intent') or '')[:60]}"
        )
        if item.get("weaknesses"):
            md.append(f"  - weaknesses: {'; '.join(item['weaknesses'][:3])}")
    md.append("")
    (out_dir / "analysis.md").write_text("\n".join(md), encoding="utf-8")
    return report


def main() -> int:
    """批量增强与评估入口。"""
    p = argparse.ArgumentParser(description="100 条样例批量测评")
    p.add_argument(
        "--intents-file",
        type=Path,
        default=ROOT / "input" / "eval100_intents.txt",
    )
    p.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "input" / "eval100_manifest.jsonl",
    )
    p.add_argument(
        "--out-root",
        type=Path,
        default=ROOT / "runs" / f"eval100_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    )
    p.add_argument("--limit", type=int, default=0, help="只跑前 N 条，0=全部")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--skip-enhance", action="store_true")
    p.add_argument("--skip-judge", action="store_true")
    p.add_argument("--skill-router", default="hybrid")
    p.add_argument("--mechanism-router", default="off")
    args = p.parse_args()

    cases = _load_cases(args.manifest, args.intents_file)
    if args.offset:
        cases = cases[args.offset :]
    if args.limit and args.limit > 0:
        cases = cases[: args.limit]
    args.out_root.mkdir(parents=True, exist_ok=True)
    print(f"cases={len(cases)} out={args.out_root}")

    results: list[dict] = []
    failures: list[dict] = []
    for i, case in enumerate(cases, 1):
        cid = case["id"]
        intent = case["intent"]
        run_dir = args.out_root / cid
        print(f"[{i}/{len(cases)}] {cid} focus={case.get('focus')} ...")
        try:
            if not args.skip_enhance:
                rec = enhance(
                    "t2va",
                    intent,
                    out_dir=run_dir,
                    skill_router=args.skill_router,
                    mechanism_router=args.mechanism_router,
                    enable_verify=True,
                )
                # 把 case_id 写入 run.json 便于回溯
                run_json = run_dir / "run.json"
                if run_json.is_file():
                    data = json.loads(run_json.read_text(encoding="utf-8"))
                    data["case_id"] = cid
                    data["focus"] = case.get("focus")
                    run_json.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                print(f"  enhanced → {run_dir / 'prompt.txt'}")
            else:
                if not (run_dir / "prompt.txt").is_file() and not (run_dir / "run.json").is_file():
                    raise FileNotFoundError(f"缺少增强产物: {run_dir}")

            if not args.skip_judge:
                ev = evaluate_run_dir(run_dir)
                ev.setdefault("package", {})["case_id"] = cid
                ev["package"]["focus"] = case.get("focus")
                # 覆写 eval.json 带上 case_id
                from src.judge import write_eval_artifacts

                write_eval_artifacts(run_dir, ev)
                results.append(ev)
                print(f"  judge overall={ev.get('overall')}")
        except Exception as exc:  # noqa: BLE001
            failures.append({"id": cid, "error": str(exc)})
            print(f"  FAILED: {exc}")
            traceback.print_exc()

    (args.out_root / "failures.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if results:
        report = _analyze(results, cases, args.out_root)
        print(
            f"done overall_mean={report.get('overall_mean')} "
            f"weakest={report.get('weakest_dimensions')[:3]} → {args.out_root / 'analysis.md'}"
        )
    else:
        print("无成功评估结果")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
