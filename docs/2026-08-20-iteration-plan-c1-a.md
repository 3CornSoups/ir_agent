# 小迭代计划：C1 + A（B 合并进 A）

> 日期：2026-08-20  
> 目标：在不改 Python 管线的前提下，用 prompt 层修复两类退化——简单短视频过度膨胀、参考图视觉属性衰减。  
> 关联文档：[2026-08-19-pipeline-issues-and-improvements.md](../2026-08-19-pipeline-issues-and-improvements.md)

---

## 0. 审查结论（相对初稿）

初稿方向正确：**衣服消失**主要是 inventory → expand → elaborate → format 的有损转写 + H3 用先验补洞；**简单镜头物理怪**主要是固定膨胀偏置 + 不可执行物理量稀释有效控制词。下列问题已并入本版再执行。

| 问题 | 为何必须改 | 本版处理 |
|------|------------|----------|
| 只写「保留衣着」，没写「禁止降覆盖」 | 用户现象是「图里有衣服 → 成片没衣服」。省略和**主动写成裸露/泛化成人**是两条路；后者更常见 | VISUAL IDENTITY LOCK 增加 `MAY NOT`：nude / shirtless / reduced coverage，除非 inventory 或意图写明 |
| C1 与 A 互相打架 | 「每件衣服都必须写」+「单镜头一段」会让模型二选一，通常丢掉衣着去写短 | 写明：缩放的是 padding，不是 identity；衣着用紧凑清单即可 |
| 物理约束只加在 elaborate | expand 已要求 `action physics`，精度量会在第一轮写进去，elaborate 很难全删 | expand 改为 action-level motion，并禁止 m/s、g/m²、焦距数字 |
| 同一套 LOCK 写三遍 | prompt 变长，Gemini 更容易忽略后半段 | expand / elaborate 各写一次完整 LOCK；format 只加一句身份，不复述整段 |
| 忽略 perceive | C1 只能锁定 **inventory 里已经写下的**属性。感知漏写衣服，后面无法「还原」 | 给 `perceive_image.txt` / `perceive_refs.txt` 各加一条 coverage 硬要求（仍只改 prompt） |
| `equal or greater specificity` | 鼓励把衣着写得比库存更长，加重简单镜头膨胀 | 改为 equal specificity，允许 compact list |
| Phase 0 四用例直播对比 | 依赖 Gemini/H3 与素材，不能挡住 prompt 落地 | 本迭代执行：改 prompt + 快照测试 + pytest。出片 A/B 仍为人工验收 |

**仍解释不了、也不在本迭代修的部分**：H3 在图文冲突时可能仍按人体先验出片。prompt 只能降低「文本与参考图打架」的概率，不能保证成片像素级跟图。那是 C2/C3 和模型侧问题。

### 0.1 复核修订（2026-08-20 执行前）

初稿 + §0 审查仍漏了三处，已在执行前修正并同步进 §3：

| 漏点 | 后果 | 修正 |
|------|------|------|
| `_elaborate_user()` 的 USER 消息仍写 `Expand the scene note below to official Context-IR detail level` | USER 指令约束力高于 SYSTEM，会抵消 elaborate 的复杂度缩放（成功标准 1 直接失败） | `src/pipeline.py` 该句改为 `Match detail depth to the scene's complexity; do not pad a simple single-shot clip...`。属 prompt 文案的 1 行修改，非编排逻辑，仍零编排改动 |
| elaborate 物理约束写 `Do NOT add micro-details` | expand 已写进的物理精度（0.6m/s 等）不是「新增」，不会被要求清除 | 改为 `Do NOT write or retain` |
| expand 物理约束放在 inventory 分支的 LOCK 内 | T2VA 无参考图时 expand 阶段不禁物理精度 | 移出 LOCK，改为全模式通用 `Physics constraint (all modes)` |

---

## 1. 迭代范围

### 1.1 本迭代做什么

| 编号 | 内容 | 改动文件 |
|------|------|----------|
| **C1** | 视觉属性锁定 + 禁止降覆盖 | `prompts/expand_intent.txt`、`prompts/elaborate.txt`、`prompts/format_h3.txt` |
| **C1-pre** | 感知阶段必须写清衣着与覆盖 | `prompts/perceive_image.txt`、`prompts/perceive_refs.txt` |
| **A** | 复杂度自适应缩放（简单单镜头少写、不写满） | `prompts/elaborate.txt`、`prompts/format_h3.txt` |
| **B→A** | 禁止不可执行物理精度描述 | `prompts/elaborate.txt`、`prompts/expand_intent.txt` |

### 1.2 本迭代不做什么

- **C2**：`pipeline.py` 里 `_format_user()` 提取 visual lock block（留 Phase 2 backlog）
- **C3**：`verify.py` 语义视觉一致性检查（留 Phase 2 backlog）
- **代码级复杂度路由**：简单任务跳过 `elaborate`（留 Phase 3，需改 `pipeline.py`）
- **H3 出片对比跑批**：本迭代只改 prompt + 文本级回归；出片 A/B 作为人工验收项

### 1.3 成功标准

1. **简单单镜头**（4–5 秒、1 shot、无切镜）：elaborate 指令不再要求对齐官方长稿；不含风速、克重、焦距等不可执行物理量指令。
2. **参考图保真**：有 inventory 时，prompt 明确禁止省略/泛化/降覆盖；感知要求写清 clothing + coverage。
3. **复杂多镜头内容不退化**：原有 `test/test_pipeline.py` 结构校验仍全绿；复杂用例 prompt 字段骨架不变。
4. **与官方对齐**：字段名、对齐句、标签规则不被 prompt 改动破坏。
5. **A 与 C1 可并存**：elaborate 同时包含 complexity scaling 与 identity lock，并写明缩放对象是 padding。

---

## 2. 设计原则

1. **只改 prompt，不动编排**：保持 `perceive → expand → elaborate → format → verify` 步序不变，降低回归面。唯一例外：`_elaborate_user()` 的 1 行 USER 文案（仍是 prompt 文本，只是宿主在代码里），见 §0.1。
2. **硬约束写清楚，软约束不堆叠**：新增段落用 `MUST / MAY NOT / Do NOT` 句式；同一 LOCK 不在三文件全文复制。
3. **A 与 C1 分工明确**：
   - **C1** 管「写什么不能丢、不能降覆盖」
   - **A** 管「写多少合适」
   - **B** 管「怎么写物理」（动作级语言，不写精度量）
4. **不引入新 LLM 调用**：零 API 成本增加。

---

## 3. 文件级改动清单

### 3.1 `prompts/expand_intent.txt`

**替换第 3 行** `action physics` → `action-level motion`（避免第一轮就写精度物理）。

**删除**：
```text
If a reference inventory is provided, stay consistent with it. Do not invent extra files.
```

**新增（C1 + B，只在此处写完整 LOCK）**：
```text
VISUAL IDENTITY LOCK — when a reference inventory is provided:
- Inventory visual attributes are mandatory, not optional "details that serve the intent".
- Preserve each listed attribute at equal specificity (a compact list is fine): clothing type, color, body coverage, fabric/texture, accessories, hairstyle, skin-visible vs covered areas.
- You MAY NOT omit, generalize, or replace any such attribute (e.g. do not turn "white long coat, buttoned, lapel collar, black turtleneck underneath" into "well-dressed" or drop the coat).
- You MAY NOT describe a subject as nude, shirtless, unclothed, or with reduced coverage unless the inventory or user intent explicitly says so.
- Do not invent extra files or subjects not in the inventory.

Physics constraint (all modes):
- Do not write numeric physics a video model cannot execute (wind m/s, fabric g/m², exact focal length).
```
（修订：物理约束为全模式通用，移出 inventory 分支，T2VA 同样生效。）

**MODE 句只加一词，不复述 LOCK**：I2VA 的 Keep 句补 `coverage` 与 `never contradict the still`。

### 3.2 `prompts/elaborate.txt`

**替换第 1 行**为 complexity matching（不再对齐官方长稿详略）。

**VISUAL 维度**：`micro-motions and facial expressions` → `visible motion and expression (only when the beat requires it)`。

**Hard constraints 后追加 Complexity scaling + VISUAL IDENTITY LOCK**（含 restore dropped attributes、禁止降覆盖、padding vs identity）。
（修订：物理约束由 `Do NOT add micro-details` 改为 `Do NOT write or retain`，覆盖 expand 已写进稿件的精度量。）

**`_elaborate_user()` USER 消息**：`Expand the scene note below to official Context-IR detail level` → 改为复杂度匹配文案（修订见 §0.1，这是唯一的非 prompt 文件改动，属 1 行文案，非编排）。

**末行**改为 one concise paragraph unless multiple shots。

### 3.3 `prompts/format_h3.txt`

**Shared constraints**：删除 `full shot-by-shot production description`，改为单镜头写清 subject/action/environment/sound，多镜头才更细。

**i2va / fl2va / l2va / r2va**：各加一句身份/覆盖，不贴整段 LOCK。

### 3.4 `prompts/perceive_image.txt` / `perceive_refs.txt`

人物/动物必须写 clothing items、colors、body coverage（covered vs exposed）；可见衣着不得省略。

---

## 4. 实施步骤

### Phase 0：基线采样（可选，不阻塞落地）

有素材与 Gemini 时，对 S1–S4 各跑 1 次 `--no-video` 存 `runs/`。无则跳过，用 git 对比 prompt 即可回滚。

### Phase 1：改 prompt

1. 按 §3 修改五个 prompt 文件
2. 通读：无字段名冲突；A 与 C1 以「缩放 padding、锁定 identity」为准
3. 更新 `2026-08-19-pipeline-issues-and-improvements.md` §四 状态为「C1+A 已实施」

### Phase 2：文本级回归

在 `test/test_pipeline.py` 增加 prompt 内容断言（不调用 Gemini）。然后：

```bash
pytest test/test_pipeline.py test/test_verify.py -q
```

### Phase 3：出片验收（人工）

对简单 i2va / t2va 跑 `--compare-video`，看衣着与物理是否改善、复杂用例是否退化。

---

## 5. 风险与缓解

| 风险 | 说明 | 缓解 |
|------|------|------|
| 复杂内容变「太瘦」 | A 的 scaling 可能让多镜头也写短 | 明确 multi-shot 可更长 |
| C1 过度字面复制 | 模型把 inventory 整段粘贴 | equal specificity + compact list |
| format 与 elaborate 冲突 | format 仍注入官方长范例 | 只改 shared constraint 一句 + 身份句 |
| prompt 变长 | Gemini 忽略后半段 | LOCK 只完整出现两次；format/perceive 各一句 |
| H3 仍脱掉衣服 | 图文权重不够 | 记入 C2/C3 backlog，不假装本迭代能根治 |

---

## 6. 回滚方案

回滚 = `git checkout` 五个 prompt 文件 + `src/pipeline.py`（若含 §0.1 的 `_elaborate_user` 文案改动）。配置无改动。

---

## 7. 工期估算

| 阶段 | 工时 |
|------|------|
| Phase 1 改 prompt | 0.5–1h |
| Phase 2 文本回归 | 0.5h |
| Phase 3 出片验收 | 2–4h（人工，本会话不做） |

---

## 8. Phase 2 backlog（本迭代不做）

1. **C3**：verify 视觉一致性 LLM 检查
2. **C2**：`_format_user()` 注入 Visual lock checklist
3. **复杂度路由**：简单场景跳过 elaborate

---

## 8.5 交付配套（2026-08-20 追加）

为服务器部署新增一键脚本与运行日志（独立于 C1+A 的 prompt 改动，可单独回滚）：

| 项 | 说明 | 文件 |
|----|------|------|
| 一键脚本 | `python scripts/oneclick_run.py -m t2va --intent "..."`；默认只增强不出片，`--video` 出片 | `scripts/oneclick_run.py`（新增） |
| 运行日志 | 每次运行在 `log/run_<模式>_<时间戳>_<序号>/` 按 stage 成对写 request/response；`meta.json` 记意图/模式/结果路径；`log/` 已 gitignore | `src/runlog.py`（新增）、`src/gemini.py`（chat 接入）、`src/video.py`（h3_create/h3_query 接入） |
| 回归 | `test/test_runlog.py`（7 用例）；全套件通过（`--ignore=test_agent_vs_official.py`，依赖未挂载网络路径） | `test/test_runlog.py`（新增） |

## 9. 验收签字表

| 项 | 标准 | 结果 |
|----|------|------|
| C1 expand | VISUAL IDENTITY LOCK + 禁止降覆盖 | ☑ prompt 已改 |
| C1 elaborate | 同上 + restore dropped attributes | ☑ prompt 已改 |
| C1-pre perceive | clothing + coverage 写入感知 prompt | ☑ prompt 已改 |
| A elaborate | complexity scaling；padding vs identity | ☑ prompt 已改 |
| B→A | 不可执行物理约束（expand 全模式 + elaborate write/retain） | ☑ prompt 已改 |
| A format | 删除 over-pad 句子 | ☑ prompt 已改 |
| 0.1 修订 | `_elaborate_user` 去掉「官方 Context-IR 详略级别」锚点 | ☑ `pipeline.py` 1 行文案已改 |
| 测试 | pytest 全绿 | ☑ `test_pipeline.py` + `test_verify.py` + `test_runlog.py` 49 passed |
| 一键脚本 + 运行日志 | oneclick_run.py / runlog.py 端到端验证 | ☑ t2va/i2va 实跑通过，request/response 成对落盘 |
| S1/S2 出片 | 人工 | ☐ |

---

## 10. 一句话总结

**本迭代 = 五个 prompt 的 surgical edit + 1 行 USER 文案**：感知写清覆盖、expand/elaborate 锁定衣着且禁止降覆盖、简单场景少写且物理只用动作级语言、format 去掉「单镜头必须写满」、elaborate 不再要求对齐官方长稿详略；零编排改动、零新 API。成片级衣着仍可能被 H3 先验覆盖，那是下一阶段 C2/C3。
