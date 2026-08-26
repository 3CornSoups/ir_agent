# BASELINE v1 + 迭代快照（交付态）

> 评测后端：**Gemini API**（`configs/judge.yaml` → `backend: gemini`；FC/E 经 `src.gemini.chat`）。
> 连续 2 轮 full gate：`runs/gate_full_r1`、`runs/gate_full_r2`（`DONE_TWO_GATES`）。

| 指标 | baseline_v1 | 最新（交付） | 门槛 |
| --- | --- | --- | --- |
| fidelity_pass_rate S1 | **0.50** | **1.00**（`gate_full_r1/r2`，n=100） | ≥98% |
| fidelity_pass_rate S2 | 待测 | **1.00**（`gate_full_r1/r2`，n=60） | ≥95% |
| fidelity_pass_rate S3 | **0.3125** | **0.979**（`gate_full_r1/r2`，n=48；仅 a12 一类 3/4） | ≥90% |
| invention_rate | **0.50** / **0.646**（S1/S3） | S1 **0.00** / S2 **0.00** / S3 **0.021** | ≤3% |
| FC3/FC4 对白·屏上逐字 | S1 **1.00** | S1+S2+S3 **208/208**（r1/r2） | 100% |
| enrichment_median | **0.36** | F通过中位 **0.799**（r1；S1=0.829 / S3=0.833；S2=0.686） | ≥0.75 |
| W 盲测胜率 | 待测 | **0.55**（Gemini 裁判，`runs/blind_ab_w10b_gemini`）；本地F **9/10** ≥ 官方 **4/10**（`blind_ab_w10b/f_compare.json`） | ≥55% 且 F≥官方 |
| judge_rho / σ_self | 待测 | Gemini 全量40：ρ=**0.804**，σ_self=**0.009**，MAE=**0.520**（`runs/judge_calib_gemini`）；§0.4 ρ/σ 达标。附：five_ratio=0.546（高于 T0.4 的 35% 辅助闸，已记 CHANGELOG） | ρ≥0.70，σ≤0.25 |
| skill_precision @keyword | **1.00** | **1.00**（S4 n=60，recall=0.975，f1=0.987） | — |
| 全量回归无退化 | 无 | r1≡r2：S1/S2/S3 F 无下降 | 任集 F↓>1pt 回滚 |

## S3 分陷阱通过率（`gate_full_r1`，与 r2 一致）

| 陷阱 | baseline | 最新 |
| --- | --- | --- |
| A1 | 0.50 | **1.00** |
| A2 | 0.50 | **1.00** |
| A3 | 0.25 | **1.00** |
| A4 | 0.75 | **1.00** |
| A5 | 0.25 | **1.00** |
| A6 | 0.00 | **1.00** |
| A7 | 0.25 | **1.00** |
| A8 | 0.75 | **1.00** |
| A9 | 0.50 | **1.00** |
| A10 | 0.00 | **1.00** |
| A11 | 0.00 | **1.00** |
| A12 | 0.00 | **0.75** |

## 产物路径

- `runs/gate_full_r1/`、`runs/gate_full_r2/`（连续两轮 full gate）
- `runs/gate_full_prompts/`（S1/S2/S3 增强稿）
- `runs/blind_ab_w10b_gemini/`、`runs/blind_ab_w10b/f_compare.json`
- `runs/judge_calib_gemini/calibrate.json`
- `runs/baseline_v1/`（初值）

## 复现

```bash
# 裁判走 Gemini（默认）
# configs/judge.yaml → backend: gemini

python3 scripts/validate_evalset.py all
python3 scripts/gate.py --set full --prompts-root runs/gate_full_prompts --out runs/gate_full_r1
python3 scripts/calibrate_judge.py --gold input/judge_gold.jsonl --out runs/judge_calib_gemini
python3 scripts/blind_ab.py --pairs input/evalset_v2/blind_pairs_w10b.jsonl --out runs/blind_ab_w10b_gemini
PYTHONPATH=. python3 -m pytest -q
```
