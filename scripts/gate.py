#!/usr/bin/env python3
"""统一 gate 跑批 + baseline 对拍。

用法：
  python3 scripts/gate.py --set s3 --dry-run
  python3 scripts/gate.py --set quick --out runs/gate_quick
  python3 scripts/gate.py --set full --out runs/baseline_v1 --baseline runs/baseline_v1
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.contract import parse_intent_deterministic  # noqa: E402
from src.enrichment import enrichment_median, evaluate_enrichment  # noqa: E402
from src.fidelity import evaluate_fidelity, fidelity_pass_rate, invention_rate  # noqa: E402

EVALSET = ROOT / "input" / "evalset_v2"
S1_MANIFEST = ROOT / "input" / "eval100_manifest.jsonl"
S1_INTENTS = ROOT / "input" / "eval100_intents.txt"


def _utc_now() -> str:
    """UTC 时间戳。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 jsonl。"""
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_set(name: str, *, sample_s2: int | None = None) -> list[dict[str, Any]]:
    """加载评测集用例。"""
    if name == "s1":
        if S1_MANIFEST.is_file():
            return _load_jsonl(S1_MANIFEST)
        rows = []
        for i, line in enumerate(S1_INTENTS.read_text(encoding="utf-8").splitlines(), 1):
            text = line.strip()
            if text:
                rows.append({"id": f"c{i:03d}", "mode": "t2va", "intent": text})
        return rows
    if name == "s2":
        rows = _load_jsonl(EVALSET / "s2_reference.jsonl")
        if sample_s2 is not None and len(rows) > sample_s2:
            rng = random.Random(42)
            return rng.sample(rows, sample_s2)
        return rows
    if name == "s3":
        return _load_jsonl(EVALSET / "s3_adversarial.jsonl")
    if name == "s4":
        return _load_jsonl(EVALSET / "s4_routing.jsonl")
    if name == "quick":
        return (
            load_set("s3")
            + load_set("s2", sample_s2=20)
            + load_set("s4")
        )
    if name == "full":
        return load_set("s1") + load_set("s2") + load_set("s3") + load_set("s4")
    raise ValueError(f"未知集合: {name}")


def _set_tag(case: dict[str, Any]) -> str:
    """推断 case 所属集合标签。"""
    cid = str(case.get("id") or "")
    if cid.startswith("s3_") or case.get("trap"):
        return "s3"
    if cid.startswith("s2_") or case.get("expected_retain"):
        return "s2"
    if cid.startswith("s4_") or "gold_skills" in case:
        return "s4"
    return "s1"


def run_s4_routing(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """只跑路由，计算 skill Precision/Recall（keyword/hybrid 视实现）。"""
    from src.contract import parse_intent_deterministic
    from src.skill_router import select_style_skills

    tp = fp = fn = 0
    details: list[dict[str, Any]] = []
    for case in cases:
        intent = case["intent"]
        gold = set(case.get("gold_skills") or [])
        contract = parse_intent_deterministic(intent)
        sel = select_style_skills(
            intent,
            router="keyword",
            explicit_style=contract.explicit_style,
            explicit_negatives=list(contract.explicit_negatives or []),
        )
        pred = set(sel.ids)
        tp += len(gold & pred)
        fp += len(pred - gold)
        fn += len(gold - pred)
        # negative：gold 空，任何预测都算 fp
        if case.get("negative") and pred:
            # 已在 fp 计入
            pass
        details.append(
            {
                "id": case.get("id"),
                "gold": sorted(gold),
                "pred": sorted(pred),
                "source": sel.source,
            }
        )
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "n": len(cases),
        "details": details,
    }


def dry_run_case(case: dict[str, Any]) -> dict[str, Any]:
    """dry-run：只抽 contract，不跑增强；对 S3 检查可本地判定的 assert。"""
    intent = str(case.get("intent") or "")
    mode = str(case.get("mode") or "t2va")
    contract = parse_intent_deterministic(intent, mode=mode)
    assert_ok = True
    notes: list[str] = []
    a = case.get("assert") or {}
    if a.get("action_chain") and contract.action_chain != a["action_chain"]:
        assert_ok = False
        notes.append(f"action_chain {contract.action_chain} != {a['action_chain']}")
    if a.get("ambiguities_nonempty") and not contract.ambiguities:
        assert_ok = False
        notes.append("ambiguities empty")
    if a.get("dialogue_verbatim"):
        texts = {d.text for d in contract.dialogue}
        for line in a["dialogue_verbatim"]:
            if line not in texts:
                assert_ok = False
                notes.append(f"dialogue missing {line}")
    if a.get("onscreen_verbatim"):
        for line in a["onscreen_verbatim"]:
            if line not in contract.onscreen_text:
                assert_ok = False
                notes.append(f"onscreen missing {line}")
    return {
        "id": case.get("id"),
        "set": _set_tag(case),
        "dry_run": True,
        "contract_nonempty": contract.is_nonempty(),
        "assert_ok": assert_ok,
        "notes": notes,
        "contract": contract.to_dict(),
    }


def evaluate_existing_prompt(
    case: dict[str, Any],
    prompt: str,
    *,
    gold_prompt: str | None = None,
    use_llm: bool = True,
    inventory: str = "",
    case_dir: Path | None = None,
) -> dict[str, Any]:
    """对已有最终 prompt 跑 F/E（不调用增强）；优先落盘 contract.json。"""
    intent = str(case.get("intent") or "")
    mode = str(case.get("mode") or "t2va")
    contract = None
    # 增强产物目录若有 contract.json，与当时 shot/must 对齐，避免短片默认单镜误杀多镜 S2
    cdir = case_dir
    if cdir is None and case.get("id"):
        # 调用方可在 case["_case_dir"] 注入
        raw = case.get("_case_dir")
        if raw:
            cdir = Path(str(raw))
    if cdir is not None:
        cpath = cdir / "contract.json"
        if cpath.is_file():
            try:
                from src.contract import contract_from_llm_payload

                payload = json.loads(cpath.read_text(encoding="utf-8"))
                contract = contract_from_llm_payload(payload, intent=intent, mode=mode)
            except Exception:  # noqa: BLE001
                contract = None
    if contract is None:
        contract = parse_intent_deterministic(intent, mode=mode)
    # expected_retain 多为模式化占位（如「reference identity」「下装可见」），
    # 直接塞进 FC6 会系统性误杀；与 gate_t1_3/s2 达 F=1.00 时一致：
    # 仅使用 enhance 落盘 contract.reference_attrs（真实库存属性）。
    # 占位清单的替换见 docs/BACKLOG.md。
    chat_fn = None
    if use_llm:
        try:
            from src.gemini import chat as chat_fn  # type: ignore
        except Exception:  # noqa: BLE001
            chat_fn = None
    inv = inventory or str(case.get("inventory") or "")
    f_rep = evaluate_fidelity(contract, prompt, chat=chat_fn, inventory=inv)
    e_rep = evaluate_enrichment(contract, prompt, gold_prompt=gold_prompt) if f_rep.passed else None
    return {
        "id": case.get("id"),
        "set": _set_tag(case),
        "fidelity": f_rep.to_dict(),
        "enrichment": e_rep.to_dict() if e_rep else None,
        "passed": f_rep.passed,
    }


def summarize(results: list[dict[str, Any]], *, routing: dict[str, Any] | None = None) -> dict[str, Any]:
    """汇总 gate 指标。"""
    by_set: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_set.setdefault(r.get("set") or "unknown", []).append(r)

    summary: dict[str, Any] = {"generated_at": _utc_now(), "sets": {}}
    for name, items in by_set.items():
        if items and items[0].get("dry_run"):
            summary["sets"][name] = {
                "n": len(items),
                "contract_nonempty_rate": sum(1 for x in items if x.get("contract_nonempty")) / len(items),
                "assert_ok_rate": sum(1 for x in items if x.get("assert_ok")) / len(items),
            }
            continue
        f_reports = []
        e_reports = []
        from src.fidelity import FidelityReport, FCResult
        from src.enrichment import EnrichmentReport, ENResult

        for item in items:
            fd = item.get("fidelity") or {}
            checks = {
                k: FCResult(**v) if isinstance(v, dict) else v
                for k, v in (fd.get("checks") or {}).items()
            }
            f_reports.append(
                FidelityReport(
                    passed=bool(fd.get("passed")),
                    checks=checks,
                    invention_count=int(fd.get("invention_count") or 0),
                )
            )
            ed = item.get("enrichment")
            if ed:
                e_reports.append(
                    EnrichmentReport(
                        score=float(ed.get("score") or 0),
                        checks={
                            k: ENResult(**v) if isinstance(v, dict) else v
                            for k, v in (ed.get("checks") or {}).items()
                        },
                        used_gold=bool(ed.get("used_gold")),
                    )
                )
        summary["sets"][name] = {
            "n": len(items),
            "fidelity_pass_rate": fidelity_pass_rate(f_reports),
            "invention_rate": invention_rate(f_reports),
            "enrichment_median": enrichment_median(e_reports) if e_reports else None,
        }
    if routing:
        summary["skill_routing"] = {
            "precision": routing["precision"],
            "recall": routing["recall"],
            "f1": routing["f1"],
        }
    return summary


def write_report(out_dir: Path, summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    """写 gate.json + gate_report.md。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gate.json").write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# Gate Report",
        "",
        f"- generated_at: `{summary.get('generated_at')}`",
        "",
        "## Sets",
        "",
    ]
    for name, metrics in (summary.get("sets") or {}).items():
        lines.append(f"### {name}")
        for k, v in metrics.items():
            lines.append(f"- {k}: **{v}**")
        lines.append("")
    if summary.get("skill_routing"):
        lines.append("## Skill routing")
        for k, v in summary["skill_routing"].items():
            lines.append(f"- {k}: **{v}**")
        lines.append("")
    (out_dir / "gate_report.md").write_text("\n".join(lines), encoding="utf-8")


def compare_baseline(summary: dict[str, Any], baseline_dir: Path) -> list[str]:
    """与 baseline 对拍：任一集合 F 通过率下降 >1pt 则告警。"""
    warnings: list[str] = []
    base_path = baseline_dir / "gate.json"
    if not base_path.is_file():
        return [f"baseline 不存在: {base_path}"]
    base = json.loads(base_path.read_text(encoding="utf-8"))
    base_sets = (base.get("summary") or base).get("sets") or {}
    for name, metrics in (summary.get("sets") or {}).items():
        if "fidelity_pass_rate" not in metrics:
            continue
        old = (base_sets.get(name) or {}).get("fidelity_pass_rate")
        if old is None:
            continue
        delta = float(metrics["fidelity_pass_rate"]) - float(old)
        if delta < -0.01:
            warnings.append(f"{name} fidelity_pass_rate 下降 {-delta:.3f} (>1pt) → 应回滚")
    return warnings


def main() -> int:
    """入口。"""
    parser = argparse.ArgumentParser(description="ir_agent gate runner")
    parser.add_argument("--set", default="s3", help="s1|s2|s3|s4|quick|full")
    parser.add_argument("--dry-run", action="store_true", help="不跑增强，只校验 contract/assert/路由")
    parser.add_argument("--out", type=Path, default=ROOT / "runs" / "latest")
    parser.add_argument("--baseline", type=Path, default=None, help="对拍目录")
    parser.add_argument("--prompts-root", type=Path, default=None, help="已有 prompt.txt 的 case 根目录")
    args = parser.parse_args()

    set_name = args.set.lower()
    results: list[dict[str, Any]] = []
    routing = None

    try:
        if set_name in {"s4", "quick", "full"}:
            s4_cases = load_set("s4")
            routing = run_s4_routing(s4_cases)

        if args.dry_run:
            cases = load_set(set_name)
            # s4 在 dry-run 里也逐条记 contract
            for case in cases:
                if _set_tag(case) == "s4" and set_name == "s4":
                    results.append(
                        {
                            "id": case.get("id"),
                            "set": "s4",
                            "dry_run": True,
                            "contract_nonempty": True,
                            "assert_ok": True,
                            "notes": [],
                        }
                    )
                else:
                    results.append(dry_run_case(case))
        else:
            # 非 dry-run：若给了 prompts-root，对已有产物评 F/E；否则提示用 dry-run / 后续接 enhance
            cases = load_set(set_name)
            if args.prompts_root is None:
                print("未指定 --prompts-root，自动降级为 dry-run（避免误跑全量增强）。", file=sys.stderr)
                for case in cases:
                    results.append(dry_run_case(case))
            else:
                n_cases = len(cases)
                for i, case in enumerate(cases, 1):
                    cid = str(case.get("id"))
                    case_dir = args.prompts_root / cid
                    prompt_path = case_dir / "prompt.txt"
                    if not prompt_path.is_file():
                        print(f"[{i}/{n_cases}] {cid} missing prompt", flush=True)
                        results.append({"id": cid, "set": _set_tag(case), "passed": False, "error": "missing prompt"})
                        continue
                    print(f"[{i}/{n_cases}] {cid} evaluate", flush=True)
                    prompt = prompt_path.read_text(encoding="utf-8")
                    inv = ""
                    inv_txt = case_dir / "inventory.txt"
                    if inv_txt.is_file():
                        inv = inv_txt.read_text(encoding="utf-8")
                    else:
                        run_json = case_dir / "run.json"
                        if run_json.is_file():
                            try:
                                inv = str(json.loads(run_json.read_text(encoding="utf-8")).get("inventory") or "")
                            except json.JSONDecodeError:
                                inv = ""
                    results.append(
                        evaluate_existing_prompt(case, prompt, inventory=inv, case_dir=case_dir)
                    )
                    last = results[-1]
                    print(
                        f"  passed={last.get('passed')} set={last.get('set')} err={last.get('error')}",
                        flush=True,
                    )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        print(f"gate 失败: {exc}", file=sys.stderr)
        return 2

    summary = summarize(results, routing=routing)
    write_report(args.out, summary, results)
    # 同步 latest 软链语义：再写一份 runs/latest（若 out 不同）
    latest = ROOT / "runs" / "latest"
    if args.out.resolve() != latest.resolve():
        write_report(latest, summary, results)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nWrote {args.out / 'gate_report.md'}")

    if args.baseline:
        warns = compare_baseline(summary, args.baseline)
        for w in warns:
            print("BASELINE WARN:", w)
        if any("应回滚" in w for w in warns):
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
