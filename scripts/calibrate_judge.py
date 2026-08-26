#!/usr/bin/env python3
"""裁判校准：Spearman ρ、MAE、自一致性、分数分布。

用法：
  python3 scripts/calibrate_judge.py
  python3 scripts/calibrate_judge.py --gold input/judge_gold.jsonl --out runs/judge_calib
  python3 scripts/calibrate_judge.py --self-only --limit 20
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.eval_dimensions import DIMENSIONS  # noqa: E402
from src.judge import build_judge_system, build_judge_user, chat_judge, parse_judge_response  # noqa: E402


def _rank(xs: list[float]) -> list[float]:
    """平均秩。"""
    ordered = sorted((v, i) for i, v in enumerate(xs))
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][0] == ordered[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[ordered[k][1]] = avg
        i = j + 1
    return ranks


def spearman_rho(xs: list[float], ys: list[float]) -> float:
    """Spearman 等级相关。"""
    if len(xs) < 2:
        return 0.0
    rx, ry = _rank(xs), _rank(ys)
    n = len(xs)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    denx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    deny = math.sqrt(sum((b - my) ** 2 for b in ry))
    if denx == 0 or deny == 0:
        return 0.0
    return num / (denx * deny)


def overall_from_scores(scores: dict[str, Any]) -> float | None:
    """非空维均分。"""
    vals = [float(v) for v in scores.values() if isinstance(v, (int, float))]
    if not vals:
        return None
    return sum(vals) / len(vals)


def load_gold(path: Path) -> list[dict[str, Any]]:
    """读取金标 jsonl。"""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def judge_one(row: dict[str, Any], *, chat_fn=None) -> dict[str, Any]:
    """对一条金标样例打分。"""
    package = {
        "intent": row.get("intent") or "",
        "inventory": row.get("inventory") or "",
        "prompt": row.get("prompt") or "",
        "mode": row.get("mode") or "t2va",
        "duration": row.get("duration"),
    }
    system = build_judge_system()
    user = build_judge_user(package)
    fn = chat_fn or chat_judge
    raw = fn(system, user)
    return parse_judge_response(raw)


def main() -> int:
    """入口。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=ROOT / "input" / "judge_gold.jsonl")
    parser.add_argument("--out", type=Path, default=ROOT / "runs" / "judge_calib")
    parser.add_argument("--self-only", action="store_true", help="只跑自一致性（忽略金标相关）")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.gold.is_file():
        print(f"缺少金标: {args.gold}", file=sys.stderr)
        return 2

    rows = load_gold(args.gold)
    if args.limit:
        rows = rows[: args.limit]

    if args.dry_run:
        print(json.dumps({"n": len(rows), "gold": str(args.gold)}, ensure_ascii=False))
        return 0

    gold_overall: list[float] = []
    pred_overall: list[float] = []
    abs_err: list[float] = []
    all_pred_scores: list[float] = []
    five_count = 0
    scored_cells = 0
    self_stds: list[float] = []

    details: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        print(f"[calib {i+1}/{len(rows)}] {row.get('id')}", flush=True)
        gold_scores = row.get("scores") or {}
        g_ov = row.get("overall")
        if g_ov is None:
            g_ov = overall_from_scores(gold_scores)
        try:
            pred = judge_one(row)
        except Exception as exc:  # noqa: BLE001
            print(f"  error: {exc}", flush=True)
            details.append({"id": row.get("id"), "error": str(exc)})
            continue
        p_scores = pred.get("scores") or {}
        p_ov = pred.get("overall")
        if p_ov is None:
            p_ov = overall_from_scores(p_scores)
        print(f"  gold={g_ov} pred={p_ov}", flush=True)
        if g_ov is not None and p_ov is not None:
            gold_overall.append(float(g_ov))
            pred_overall.append(float(p_ov))
            abs_err.append(abs(float(p_ov) - float(g_ov)))
        for v in p_scores.values():
            if isinstance(v, (int, float)):
                all_pred_scores.append(float(v))
                scored_cells += 1
                if int(v) == 5:
                    five_count += 1

        # 自一致性：前 limit 或随机 20
        if i < 20:
            reps = []
            for _ in range(args.repeats):
                try:
                    r = judge_one(row)
                    ov = r.get("overall")
                    if ov is None:
                        ov = overall_from_scores(r.get("scores") or {})
                    if ov is not None:
                        reps.append(float(ov))
                except Exception:  # noqa: BLE001
                    pass
            if len(reps) >= 2:
                self_stds.append(statistics.pstdev(reps))
        details.append({"id": row.get("id"), "gold_overall": g_ov, "pred_overall": p_ov})

    rho = spearman_rho(gold_overall, pred_overall) if not args.self_only else None
    mae = (sum(abs_err) / len(abs_err)) if abs_err else None
    self_sigma = statistics.mean(self_stds) if self_stds else None
    dist_sigma = statistics.pstdev(all_pred_scores) if len(all_pred_scores) > 1 else 0.0
    five_ratio = (five_count / scored_cells) if scored_cells else 0.0

    report = {
        "n": len(rows),
        "judge_rho": rho,
        "mae": mae,
        "self_consistency_sigma": self_sigma,
        "score_sigma": dist_sigma,
        "five_ratio": five_ratio,
        "gates": {
            "rho_ge_0_70": (rho is not None and rho >= 0.70),
            "mae_le_0_6": (mae is not None and mae <= 0.6),
            "self_sigma_le_0_25": (self_sigma is not None and self_sigma <= 0.25),
            "dist_sigma_ge_0_5": dist_sigma >= 0.5,
            "five_ratio_le_0_35": five_ratio <= 0.35,
        },
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "calibrate.json").write_text(
        json.dumps({"report": report, "details": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = ["# Judge calibration", ""]
    for k, v in report.items():
        if k == "gates":
            continue
        lines.append(f"- {k}: **{v}**")
    lines.append("")
    lines.append("## Gates")
    for k, v in report["gates"].items():
        lines.append(f"- {k}: {'PASS' if v else 'FAIL'}")
    (args.out / "calibrate.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = all(report["gates"].values()) if not args.self_only else report["gates"]["self_sigma_le_0_25"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
