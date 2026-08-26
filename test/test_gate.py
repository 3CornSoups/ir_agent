"""T0.8 gate 跑批器单测：集合加载、dry-run、报告写出。"""

from __future__ import annotations

import json
from pathlib import Path

from scripts import gate as gate_mod


def test_load_s3_has_48() -> None:
    """S3 应加载 48 条。"""
    rows = gate_mod.load_set("s3")
    assert len(rows) == 48


def test_load_s4_has_60() -> None:
    """S4 应加载 60 条。"""
    rows = gate_mod.load_set("s4")
    assert len(rows) == 60
    assert sum(1 for r in rows if r.get("negative")) == 20


def test_dry_run_s3_assert(tmp_path: Path) -> None:
    """dry-run S3 应写出 gate 报告。"""
    import subprocess
    import sys

    out = tmp_path / "gate_s3"
    proc = subprocess.run(
        [sys.executable, str(gate_mod.ROOT / "scripts" / "gate.py"), "--set", "s3", "--dry-run", "--out", str(out)],
        cwd=str(gate_mod.ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert (out / "gate.json").is_file()
    assert (out / "gate_report.md").is_file()
    data = json.loads((out / "gate.json").read_text(encoding="utf-8"))
    assert data["summary"]["sets"]["s3"]["n"] == 48
    assert data["summary"]["sets"]["s3"]["contract_nonempty_rate"] == 1.0


def test_compare_baseline_detects_drop(tmp_path: Path) -> None:
    """F 通过率下降 >1pt 应告警。"""
    base = tmp_path / "baseline"
    base.mkdir()
    (base / "gate.json").write_text(
        json.dumps({"summary": {"sets": {"s3": {"fidelity_pass_rate": 0.90}}}}),
        encoding="utf-8",
    )
    summary = {"sets": {"s3": {"fidelity_pass_rate": 0.80}}}
    warns = gate_mod.compare_baseline(summary, base)
    assert any("应回滚" in w for w in warns)
