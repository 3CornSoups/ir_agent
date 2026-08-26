#!/usr/bin/env bash
# 连续两轮 full gate 重评（不对齐增强，只用 prompts-root）。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=runs/gate_full_prompts
for r in 1 2; do
  out=runs/gate_full_r${r}
  mkdir -p "$out"
  echo "=== FULL GATE ROUND $r ==="
  PYTHONUNBUFFERED=1 python3 scripts/gate.py --set full --prompts-root "$ROOT" --out "$out" --baseline runs/baseline_v1 \
    | tee "$out/console.log"
  test -f "$out/gate.json" || test -f "$out/summary.json" || test -f "$out/gate_report.md"
done
echo DONE_TWO_GATES
