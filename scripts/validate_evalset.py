#!/usr/bin/env python3
"""测试集自检：校验 S2/S3/S4 manifest 结构与可读性。

用法：
  python3 scripts/validate_evalset.py s3
  python3 scripts/validate_evalset.py s2
  python3 scripts/validate_evalset.py s4
  python3 scripts/validate_evalset.py all
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.contract import assert_verbatim_locks, parse_intent_deterministic  # noqa: E402

SET_PATHS = {
    "s2": ROOT / "input" / "evalset_v2" / "s2_reference.jsonl",
    "s3": ROOT / "input" / "evalset_v2" / "s3_adversarial.jsonl",
    "s4": ROOT / "input" / "evalset_v2" / "s4_routing.jsonl",
}


def _load_jsonl(path: Path) -> list[dict]:
    """读取 jsonl。"""
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def validate_s3(path: Path) -> list[str]:
    """校验 S3：48 条、12 类各 4、断言可 dry-run。"""
    errors: list[str] = []
    if not path.is_file():
        return [f"缺少文件: {path}"]
    rows = _load_jsonl(path)
    if len(rows) != 48:
        errors.append(f"S3 期望 48 条，实际 {len(rows)}")
    traps: dict[str, int] = {}
    for row in rows:
        trap = str(row.get("trap") or "")
        traps[trap] = traps.get(trap, 0) + 1
        if "intent" not in row or not str(row["intent"]).strip():
            errors.append(f"{row.get('id')}: 缺 intent")
            continue
        if "assert" not in row or not isinstance(row["assert"], dict):
            errors.append(f"{row.get('id')}: 缺可执行 assert")
            continue
        # dry-run：contract 可抽取 + 逐字
        try:
            c = parse_intent_deterministic(row["intent"], mode=row.get("mode") or "t2va")
            assert_verbatim_locks(c, row["intent"])
            if not c.is_nonempty():
                errors.append(f"{row.get('id')}: contract 为空")
        except AssertionError as exc:
            errors.append(f"{row.get('id')}: {exc}")
        # 动作链断言自检
        chain = row["assert"].get("action_chain")
        if chain:
            if c.action_chain != chain:
                # 允许 deterministic 与声明略有差异时仅警告式记录为 error（严格）
                errors.append(
                    f"{row.get('id')}: action_chain 抽取 {c.action_chain} != assert {chain}"
                )
        if row["assert"].get("ambiguities_nonempty") and not c.ambiguities:
            errors.append(f"{row.get('id')}: 期望 ambiguities 非空")
    for i in range(1, 13):
        key = f"A{i}"
        if traps.get(key, 0) != 4:
            errors.append(f"S3 {key} 应有 4 条，实际 {traps.get(key, 0)}")
    return errors


def validate_s4(path: Path) -> list[str]:
    """校验 S4：60 条，含 ≥20 negative。"""
    errors: list[str] = []
    if not path.is_file():
        return [f"缺少文件: {path}"]
    rows = _load_jsonl(path)
    if len(rows) != 60:
        errors.append(f"S4 期望 60 条，实际 {len(rows)}")
    neg = sum(1 for r in rows if r.get("negative"))
    if neg < 20:
        errors.append(f"S4 negative 应 ≥20，实际 {neg}")
    for row in rows:
        if not str(row.get("intent") or "").strip():
            errors.append(f"{row.get('id')}: 缺 intent")
        if "gold_skills" not in row:
            errors.append(f"{row.get('id')}: 缺 gold_skills")
        if row.get("negative") and row.get("gold_skills"):
            errors.append(f"{row.get('id')}: negative 的 gold_skills 应为空")
    return errors


def validate_s2(path: Path) -> list[str]:
    """校验 S2：60 条、expected_retain 非空、素材路径可读（若给出）。"""
    errors: list[str] = []
    if not path.is_file():
        return [f"缺少文件: {path}（T0.5 尚未交付）"]
    rows = _load_jsonl(path)
    if len(rows) != 60:
        errors.append(f"S2 期望 60 条，实际 {len(rows)}")
    for row in rows:
        rid = row.get("id")
        if not row.get("expected_retain"):
            errors.append(f"{rid}: expected_retain 为空")
        for key in ("ref_images", "ref_videos", "ref_audios"):
            for p in row.get(key) or []:
                pp = Path(p)
                if not pp.is_file():
                    errors.append(f"{rid}: 素材不可读 {p}")
    return errors


def main() -> int:
    """入口。"""
    parser = argparse.ArgumentParser(description="校验 evalset_v2")
    parser.add_argument("set", choices=["s2", "s3", "s4", "all"])
    args = parser.parse_args()
    targets = ["s2", "s3", "s4"] if args.set == "all" else [args.set]
    all_errors: list[str] = []
    for name in targets:
        path = SET_PATHS[name]
        if name == "s3":
            errs = validate_s3(path)
        elif name == "s4":
            errs = validate_s4(path)
        else:
            errs = validate_s2(path)
        if errs:
            print(f"[{name}] FAIL ({len(errs)})")
            for e in errs:
                print(" -", e)
            all_errors.extend(errs)
        else:
            print(f"[{name}] OK -> {path}")
    return 1 if all_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
