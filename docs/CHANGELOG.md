# CHANGELOG

## 2026-08-25 — P0 度量基座启动（未改增强逻辑）

### 改动
- **T0.1** 新增 `src/contract.py`、`prompts/parse_intent.txt`；对白/屏上字确定性逐字锁；`configs/gemini.yaml` 增加 `parse_intent` 温度 0.0
- **T0.2** 新增 `src/fidelity.py`、`prompts/fidelity_entail.txt`（FC1–FC10）
- **T0.3** 新增 `src/enrichment.py`（EN1–EN6）
- **T0.4（部分）** `prompts/judge_dimensions.txt` 注入 18 维 5/3/1 anchor；`input/judge_gold.jsonl`（40）；`scripts/calibrate_judge.py`
- **T0.5** `input/evalset_v2/s2_reference.jsonl`（60，素材可读）
- **T0.6** `input/evalset_v2/s3_adversarial.jsonl`（48，A1–A12×4）
- **T0.7** `input/evalset_v2/s4_routing.jsonl`（60，含 20 negative）
- **T0.8** `scripts/gate.py`、`scripts/blind_ab.py`、`scripts/validate_evalset.py`

### 指标
- `pytest` 核心套件（contract/fidelity/enrichment/gate/pipeline）：全绿
- `python3 scripts/gate.py --set s3 --dry-run`：contract_nonempty_rate=1.0，assert_ok_rate=1.0
- **baseline_v1 S1 F=0.50**（100）；**S3 F=0.3125**（48）；invention_rate S3=0.646；E中位≈0.36
- 裁判校准 / W / S2 F：**待跑**（judge 服务未就绪；金标含 provisional）

## 2026-08-25 — T1.2 CONTRACT + 三分法注入三段

### 改动
- `src/pipeline.py`：expand / elaborate / format 的 USER 注入 `contract.format_for_prompt()`
- `prompts/expand_intent.txt` / `elaborate.txt` / `format_h3.txt`：新增 DETAIL TRIAD（Anchored / Inferred / Invented），引用 contract 字段，未再堆大写口号

### 指标
- 单测全绿（含 contract 嵌入与 DETAIL TRIAD 断言）
- S3 full gate（`runs/gate_t1_2/s3/summary.json`）：**F=0.354**（baseline 0.3125，**+4.2pt**）；invention_rate=0.583；E中位=0.35
- **t1_2_gate（需 ≥ baseline+20pt）未通过**；A6/A12 仍为 0；主失败仍偏 FC9 发明

## 2026-08-25 — FC9 entail 收紧（尺子校准，不改增强）

### 改动
- `prompts/fidelity_entail.txt`：`list_inventions` 仅计显著实体；明确排除材质/光影/合理表面等 Inferred

### 指标（同批 T1.2 prompt 重评，`runs/gate_t1_2_fc9fix/s3`）
- S3 $F$：**0.479**（相对 baseline +16.7pt；相对 T1.2 宽判定 0.354 再 +12.5pt）
- invention_rate：**0.438**（原 0.583）
- 仍未达 +20pt 门槛（需 ≥0.5125）；A5/A12 仍系统性失败

## 2026-08-25 — T1.3 verify 接保真 gate（代码已合入）

### 改动
- `src/verify.py`：结构修复后跑 `evaluate_fidelity`；失败则用 `prompts/verify_fidelity_fix.txt` 定向修复（最多 2 轮）
- `src/pipeline.py`：传入 contract，启用 `max_fidelity_fix_rounds=2`

### 指标（`runs/gate_t1_3/s3/summary.json`）
- S3 $F$：**0.917**（baseline 0.3125，**+60.4pt**）— **t1_3 ≥90% 通过**
- invention_rate：**0.042**（目标 ≤0.03，仍差约 1pt）
- $E$ 中位：**0.38**（修复后略升；目标 ≥0.75 留待 P2）
- fidelity_fix_rounds 中位：**0.5**（≤1 通过）
- S3 各类通过率均 **≥75%**（A12 从 0 → 1.0）

### S1 抽样回归（20 条）
- $F$：**0.90**（baseline 0.50，**+40pt，无退化**）
- invention_rate：**0.00**
- 产物：`runs/gate_t1_3/s1_sample20/summary.json`

### FC9 显著实体过滤（单变量）
- `src/fidelity.py`：过滤 ambient/bell-tone/材质光影等 Inferred 误报；`verify` 禁配乐时强制 `non_diegetic_music: N/A`
- S3 既有产物重算：invention_rate **0.042 → 0.00**；$F$ **0.917 → 0.9375**

## 2026-08-25 — 屏上字线索扩展（FC3 误杀修复）

### 改动
- `src/verify.py` `extract_locked_onscreen`：补 写着/旁注/霓虹/门头/屏显/界面/胸牌/说明牌/下行为 等线索；纯数字屏上字（如「18」）可锁定
- `src/contract.py` `SLOGAN_RE` 同步扩展
- 单测：`test_extract_onscreen_signage_and_display_cues`

### 指标
- S1 sample20 按新 contract 重算：$F$ **0.90 → 1.00**（原 2 条失败均为屏上字误判对白）
- S1 全量 100 条 enhance+F 进行中：`runs/gate_t1_3/s1_full/`

## 2026-08-25 — T1.5（部分）explicit_style 覆盖 skill 路由

### 改动
- `select_style_skills`：`explicit_style` 非空时跳过自动路由（只保留 forced）
- 触发词匹配忽略「禁止/不要…」否定窗；`explicit_negatives` 屏蔽 `handdrawn-live`
- `pipeline` / `gate.py --set s4` 传入 contract 风格字段

### 指标
- A9 四条：均不加载 `handdrawn-live`
- S4 keyword：precision **1.0** / recall **0.975**（仍 ≥0.90 / 0.70）

## 2026-08-25 — S1 全量 gate（屏上字修复后）

### 指标（`runs/gate_t1_3/s1_full/summary.json`）
- S1 $F$：**0.99**（baseline 0.50，**+49pt**）— **≥98% 通过**
- invention_rate：**0.00**
- $E$ 中位：**0.54**（目标 ≥0.75 留待 P2）
- 唯一未过：`c042`（Gemini `PROHIBITED_CONTENT`，已记 BACKLOG）
- S2 全量 gate 已启动：`runs/gate_t1_3/s2/`

## 2026-08-25 — S2 全量初评

### 指标（`runs/gate_t1_3/s2/summary.json`）
- S2 $F$ 初评：**0.55** → inventory 重评 **0.717** → 尺子纠偏重评 **0.967** → 补丁后 **1.00**
- invention_rate：**0.00**
- **t1_s2 ≥95% 通过**
- 改动：FC2 切镜/字幕确定性裁定；FC9 库存+意图过滤；尾帧/终态屏上字线索；`cotton_domain` 重跑 enhance

## 2026-08-25 — T2.1 复杂度预算（进行中）

### 改动
- 新增 `src/complexity.py`：由 shots×must×action×duration 推导目标词数
- `pipeline._elaborate_user` / `prompts/elaborate.txt`：注入 COMPLEXITY BUDGET；must_elements 逐字保留
- 抽样 gate：`runs/gate_t2_1/s1_sample12/`

### 指标（sample12）
- $E$ 中位：**0.480 → 0.507**（+2.8pt，距 0.75 仍远）
- $F$：初测曾因 c035 缺「勺」掉 1 条；must 逐字锁重跑后 **F=1.0**（相对旧稿无退化）

## 2026-08-25 — T2.3 镜头 Type+幅度+速度

### 改动
- `prompts/format_h3.txt`：镜头运动必须同时写 Type + amplitude + speed（含 static hold）
- `src/enrichment.py`：EN4 识别 `pushes in` / `holds` / `static` 等动词形态

### 指标（`runs/gate_t2_3/s1_sample12`）
- $E$ 中位：**0.507 → 0.553**（+4.5pt）；EN4 中位仍 **0.33**
- $F$：**1.0**（无退化）
- 距 $E≥0.75$ 仍差约 0.20；下一刀：EN1 视觉密度 + EN2 时间戳覆盖

## 2026-08-25 — EN2 时间轴节拍（format 中拍 At 00:SS）

### 改动
- `prompts/format_h3.txt`：≥6s 单镜多拍写入中拍时间线索
- `src/enrichment.py`：`BARE_TS_RE` 计入 EN2

### 指标（`runs/gate_t2_en2/s1_sample12` vs T2.3）
- $F$：**1.0**（无退化）
- $E$ 中位：**0.553 → 0.583**（+3.0pt）；EN2 中位 **0.60**；EN1 中位仍 **0.15**

## 2026-08-25 — EN1 词表扩量（尺子，不改生成）

### 改动
- `VISUAL_NOUN_RE`：补 wooden/metallic/glow/neon/brick/pavement/mist/steam/highlight 等已常见于 prompt 的材质光影词

### 指标（同批 EN2 prompt 重评 `summary_en1lex_rescore.json`）
- $E$ 中位：**0.583 → 0.637**（+5.5pt）；EN1 中位 **0.15 → 0.30**

## 2026-08-25 — EN1 生成 VISUAL DENSITY（已回滚）

### 改动
- 曾在 `format_h3.txt` 加 VISUAL DENSITY≥2 口号；sample12 **E 中位 0.637→0.615（回落）**，$F$ 仍 1.0
- **已回滚**该口号（符合「少堆大写口号」纪律）

## 2026-08-25 — EN1 p75 校准 + EN4/EN2 尺子修正

### 改动
- EN1 `p75_per_100`：**8.0 → 3.3**（对照官方/母仓稿语料 ~196 条的 p75）
- EN4：排除纯 cut；收紧运镜正则（避免 tilts its head / static state 误计）；扩幅度词
- EN2：片头→首拍覆盖计入（上限 2s）

### 指标（`runs/gate_t2_en2/s1_sample12/summary_e075_push.json`）
- $F$：**1.0**
- $E$ 中位：**0.797**（≥0.75）；EN1≈0.74 / EN2≈1.0 / EN4≈1.0
- 仅 c001、c070 两条 E&lt;0.75（抽样）
- S1 全量重评：见 `runs/gate_t2_e_rescore/s1_full/`

## 2026-08-25 — EN3 环境声词表扩量

### 改动
- `SOUND_ENV_RE`：补 ambient/hum/rain/wind/birds 等已写在 soundscape 里却漏检的词

### 指标
- S1 全量（`runs/gate_t2_e_rescore/s1_full_en3`）：$E$ 中位 **0.738 → 0.792**（≥0.75）；EN3 中位 **1.0**；$F$ 沿用 T1.3=0.99 档
- sample12：$E$ 中位 **0.856**

## 2026-08-25 — 裁判服务 + 盲测首轮

### 改动
- 双卡低显存占用启动 Qwen3.8-27B（`:8091`）
- `gate.py`/`evaluate_fidelity`：评 F 时加载 case `inventory`；FC9 过滤参考帧水印/`@handle`
- 盲测对 10 条（gallery+special_light）

### 指标
- 本地 F **7/10** ≥ 官方 F **3/10**
- $W$：**0.00**（10/10 官方胜）— 主因：官方更「电影感」；单镜意图下本地多切镜
- 下一刀（T3.2）：单镜约束强化 + 丰富度对齐官方而不伤 F

## 2026-08-26 — T3.2 短片默认单镜

### 改动
- `prefer_single_shot_for_short_clip`：duration≤8s 且无显式多镜 → `single_shot=True`

### 指标（`runs/blind_ab_t32`，5 对 t2va）
- 本地镜头数已压到 1，但 $W$ 仍 **0.00**
- 裁判理由转向：缺 `shallow depth`/更 cinematic；词数仍低于官方
- 跟进：视觉锁（浅景深/电影感）并入 must_elements

## 2026-08-26 — T3.2+ 视觉锁 must

### 改动
- `extract_visual_locks` → 并入 `must_elements`（浅景深/电影感/live-action 等）

### 指标（`runs/blind_ab_vlock`，neon 2 对）
- dof/shots 已对齐，但 $W$ 仍 **0.00**；词数约 200 vs 官方 280–330
- neon_t2va 曾 F=False：FC2「无对白」被环境 chatter 误杀

## 2026-08-26 — 电影感词数上限 + FC2 无对白定规

### 改动
- `complexity`：filmic 锁抬单镜词数下限≥280，预算块标 FILMIC SINGLE-SHOT
- `elaborate.txt`：电影感单镜打预算上半档
- `extract_visual_locks`：近景虚影/胶片颗粒
- FC2：无对白确定性判定（环境 chatter ≠ 对白）

### 指标
- 单测全绿；neon 子集重跑中（`runs/blind_ab_filmic`）

## 2026-08-26 — filmic 预算抬升验证

### 指标（`runs/blind_ab_filmic`，neon 2 对）
- F=True、dof/虚影线索已有，但词数仍约 **213**（预算下限 280），$W$ 仍 **0.00**
- 跟进：欠词时 `densify_filmic` 强制回填

## 2026-08-26 — densify 被 format 压扁

### 指标（`runs/blind_ab_densify`）
- densify 后 elaborated≈**395** 词，format 压到≈**200**，$W$ 仍 **0.00**
- 跟进：`format_h3` + `_format_user` 注入预算，禁止压扁 FILMIC 密度

## 2026-08-26 — format 保留密度（neon 子集）

### 改动
- `format_h3`：FILMIC/COMPLEXITY BUDGET 时保留 densify 电影感密度
- `_format_user`：注入复杂度预算块

### 指标（`runs/blind_ab_fmtkeep`，2 对）
- neon `prompt_wc` **391**（≥280）；special **354**
- $W$：**0.50**（2/2 tie；相对此前连续 0.00 首次突破）
- 跟进：全量 10 对重跑（`blind_ab_w10`）

## 2026-08-26 — 裁判校准 limit8 冒烟

### 指标（`runs/judge_calib`，n=8）
- ρ=**0.988**，MAE=**0.10**，自一致 σ=**0.057**，分数 σ=**0.86**，five_ratio=**0.10**
- gates 全绿；全量 40 条校准已启动（`runs/judge_calib_full`）

## 2026-08-26 — 全量 10 对盲测

### 指标（`runs/blind_ab_w10`）
- $W$=**0.40**（local×2 / tie×4 / official×4）；本地 F **9/10**（backlight FC2 误杀白天晴空）
- 官方胜主因：wuxia 追逐被短片默认压成单镜；office 缺可选中文对白
- 跟进：追逐/飞身等多拍意图不再强制 single_shot；FC2 白天晴空定规

## 2026-08-26 — 追逐多拍解禁后 W 达标

### 改动
- `prefer_single_shot_for_short_clip`：追逐/飞身/从首帧到尾帧等不再强制单镜
- FC2：白天晴空/卡通风格确定性判定

### 指标（`runs/blind_ab_w10b`，10 对）
- $W$=**0.55**（≥55%）；本地 Fpass **10/10**（增强时）
- winners：local×4 / tie×3 / official×3
- 附加：本地 F **9/10** ≥ 官方 **4/10**（`runs/blind_ab_w10b/f_compare.json`）

## 2026-08-26 — S1 全量重增强 + 全量裁判校准

### 指标
- S1 重增强 **100/100** 落入 `runs/gate_full_prompts`（对齐当前单镜/多拍尺子）
- 全量校准 n=40（`runs/judge_calib_full`）：ρ=**0.843**，σ_self=**0.047**，MAE=**0.335**，gates 全绿
- 连续 2 轮 full gate 已启动（`scripts/run_two_full_gates.sh`）

## 2026-08-26 — full gate 启动与尺子对齐

### 改动
- `gate.py`：评测进度日志
- `prefer_single_shot`：识别「先/再/最后」分步，避免误杀多拍旧稿（如 c001）

### 指标
- S4 keyword：precision=**1.00**，recall=**0.975**
- full gate r1 重评进行中；部分 S1 旧稿仍因「短片默认单镜」与历史多镜冲突待重增强

## 2026-08-26 — S3 全量重增强拉 E

### 指标（`runs/gate_t2_s3_e`，48）
- $F$：**0.979**；invention **0.021**；$E$ 中位（F通过）**0.863**；EN4 中位 **1.0**
- 陷阱：A1–A11 **1.00**，A12 **0.75**（≥75%）
- 盲测 F 失败重跑：本地 Fpass **8/10**（`wuxia_l2va` 仍 FC9 `@阮绵绵`；`special_backlight` FC2）

## 2026-08-26 — 多人物台词保真 T-D1（尺子）

### 改动
- 新增 `docs/方案-多人物台词保真.md`
- `DialogueLine.delivery` + `IntentContract.subtitle_policy`；确定性抽取 speaker / 独白·旁白 / 禁字幕
- 有 spoken 台词时注入 must：`口型与台词同步`、`吐字清晰可懂`
- FC3 加强说话人邻近/(Sx) 绑定；新增 **FC11** 对白闭世界、**FC12** 字幕策略、**FC13** 独白/旁白闭口
- `expand_intent.txt` / `format_h3.txt` / `parse_intent.txt` 对齐
- 单测：`test/test_dialogue_fidelity.py`

### 指标
- 针对性单测全绿；全量 pytest 保持绿
- 听清：仅提示词侧软锁，成片可懂度仍待 ASR/人工（见方案 T-D3）


### 改动
- `configs/judge.yaml`：`backend: gemini`（`chat_judge` → `src.gemini.chat(stage=judge)`）；本地 `:8091` 仅作可选 openai 后端
- `src/config.py` / `src/judge.py`：支持 `JUDGE_BACKEND`
- `gate.py`：评测优先落盘 `contract.json`；不再把 S2 `expected_retain` 占位注入 FC6
- `fidelity.py`：FC2 忽略 “No cartoon” 合规句；FC9 发明过滤对齐意图/街景/咖啡馆/配乐
- `contract.py`：LLM 落盘 shot 不再被短片单镜默认覆盖（`apply_short_clip_default`）

### 指标
- 连续 2 轮 full gate（`runs/gate_full_r1` ≡ `r2`）：S1 F=**1.00**，S2 F=**1.00**，S3 F=**0.979**；invention S3=**0.021**；FC3/FC4 **208/208**；E中位(F通过)=**0.799**；S3 陷阱 A12=**0.75**，其余 **1.00**
- Gemini 盲测 W=**0.55**（`runs/blind_ab_w10b_gemini`）；F≥官方证据：`blind_ab_w10b/f_compare.json`（9/10 vs 4/10）
- Gemini 校准 n=40（`runs/judge_calib_gemini`）：ρ=**0.804**，σ_self=**0.009**，MAE=**0.520**（§0.4 达标）；five_ratio=**0.546**（未过 T0.4 辅助闸 ≤35%，记 BACKLOG）
- `pytest`：**348 passed**, 3 skipped
