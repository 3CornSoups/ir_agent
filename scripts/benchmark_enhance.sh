#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

OUT_ROOT="output"
SUMMARY="$OUT_ROOT/timing_summary.csv"
echo "mode,case,enhance_seconds,out_dir" > "$SUMMARY"

echo "=== ir_agent 增强耗时基准 ==="
echo "各用例 timing.json → output/<mode>_cases/<case>/"
echo ""

run_case() {
  local mode="$1"
  local case="$2"
  local out_dir="$OUT_ROOT/${mode}_cases/$case"
  shift 2
  echo "--- [$mode] $case → $out_dir ---"
  if python3 scripts/run.py -m "$mode" "$@" --out-dir "$out_dir" --no-video; then
    local seconds
    seconds=$(python3 -c "import json; print(json.load(open('$out_dir/timing.json'))['enhance_seconds'])")
    echo "[TIME] ${mode}/${case}: ${seconds}s"
    echo "$mode,$case,$seconds,$out_dir" >> "$SUMMARY"
  else
    echo "[FAIL] $mode/$case"
    echo "$mode,$case,FAIL,$out_dir" >> "$SUMMARY"
    return 1
  fi
  echo ""
}

run_case t2va case1 \
  --intent-file test_cases/t2va_cases/case1/case1.txt

run_case i2va case_dog \
  --intent-file test_cases/i2va_cases/case_dog.txt \
  --first-frame test_cases/i2va_cases/dog.png

run_case fl2va case_sol \
  --intent-file test_cases/fl2va_cases/case_sol_fl2va.txt \
  --first-frame test_cases/fl2va_cases/case_sol_firstframe.jpg \
  --last-frame test_cases/fl2va_cases/case_sol_lastframe.png

run_case l2va case_sol \
  --intent-file test_cases/l2va_cases/case_sol.txt \
  --last-frame test_cases/l2va_cases/soL.png

run_case r2va case1 \
  --intent-file test_cases/r2va_cases/case1/case1.txt \
  --ref-image test_cases/r2va_cases/case1/man.jpg \
  --ref-image test_cases/r2va_cases/case1/luobo.png \
  --ref-image test_cases/r2va_cases/case1/background.png \
  --duration 8

echo "=== 汇总 → $SUMMARY ==="
column -t -s',' "$SUMMARY" 2>/dev/null || cat "$SUMMARY"
total=$(awk -F, 'NR>1 && $3!="FAIL" {s+=$3} END {printf "%.1f", s+0}' "$SUMMARY")
echo ""
echo "合计: ${total}s"
