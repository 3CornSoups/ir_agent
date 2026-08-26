# 替代官方 Context-IR：提示词质量增强管线设计

> 日期：2026-08-19
> 状态：已批准（用户确认方案 A）

## 1. 背景与目标

**目标**：本系统只输出高质量提示词，替代官方 H3-Context-IR 的提示词增强环节。用户后续用本地/云端 H3 分别对两套提示词出片做人工对比。

**官方 Context-IR 的优势**：多阶段工作流（指令解析 → 跨模态关联 → 时序理解 → 结构化输出 → 逻辑推理），输出极为详尽（I2VA 示例 completion 达 ~10k tokens）。官方明确建议开发者按 "Prompting Guidance" 自建上下文处理系统。

**当前差距**（用户反馈）：
1. 详尽度不足（`not_detailed`）
2. 参考文件利用不足（`ref_underuse`）
3. 字段结构不稳（`structure_weak`）
4. 意图偏差 / 新增不存在的剧情（`intent_drift`）
5. 音效/音乐设计弱（`sound_weak`）
6. **台词发音紊乱**（`<d>` 语言标签与内容不匹配）
7. **画面诡异**（细节缺失导致生成逻辑不通）
8. **丢失参考素材**（标签未定义/未引用，编号超界）

**约束**：
- 本系统只输出提示词；不负责本地 H3 出片对接（用户明确"这一块先不做"）
- 允许增加 HTTP 调用次数（用户确认质量优先，每次任务 4~6 次可接受）
- 系统不改名，保持现有 CLI / 输出目录 / 报告结构兼容

## 2. 管线设计（6 步）

```
用户提示词 + 参考文件
 ① 感知    (升级) 描述事实 + 每个素材「What it can provide」
 ② 风格路由 (不变) 关键词/LLM 选择题材写法
 ③ 扩写    (增强) 场景散文，明确写出声音设计
 ④ 补细节  (新增) 对标官方详略度补全 → 解决「画面诡异/太简略」
 ⑤ 格式化  (增强) 注入官方 few-shot 范例 → 解决「字段结构不稳」
 ⑥ 质量校验 (新增) 规则硬校验 + 失败时 LLM 修复一次 → 解决「台词发音紊乱/丢素材」
```

## 3. 各阶段详细设计

### 3.1 ① 感知升级

**文件**：`prompts/perceive_image.txt`、`prompts/perceive_refs.txt`

在现有事实库存基础上，每项素材末尾追加 **`What it can provide`** 小节，分类列出该素材能提供：
- 身份外观 / 服装风格 / 光照 / 动作 / 节奏 / 音色（音频）/ 配乐风格 / 首尾帧锚点

**目的**：让后续 `subject_definitions` 正确抽象 `<Subject>`（可复用的内容单元），而不是照抄画面事实。

### 3.2 ④ 补细节（新增阶段）

**文件**：新增 `prompts/elaborate.txt`

**输入**：扩写稿 + 素材库存（可选）
**输出**：扩展后的场景散文（仍不是 MiniMax 字段）

**约束**：
- 视觉：初始构图、主体外观/位置、环境层次（前中背景）、关键道具、微动作与表情、镜头运动（类型+幅度+速度）
- 声音：环境声、动作物理声、人声；音乐：乐器 + 速度 + 节奏 + 动态变化
- 硬约束：不新增剧情/角色/结局、不删除已有 beat、不发明参考素材、画幅词禁止
- 按官方详略级别扩写：5 秒单镜头场景应覆盖上述各项

**decode 配置**：新增 `elaborate` 段（temperature 0.6，top_p 0.9）

### 3.3 ⑤ 格式化 few-shot

**文件**：`src/skill.py`（`compose_format_system`）

在 SYSTEM 中追加与当前模式匹配的官方完整范例：
- 从 `h3-prompt-writing/references/base-en.txt`（Case 1~4）提取
- 从 `h3-prompt-writing/references/ref-en.txt` 提取 r2va 完整六段示例

**方式**：在 `src/skill.py` 中为每种 MODE 选一个完整 example（含对齐句 + 三字段/六段），以 `--- Example output (official, complete) ---` 追加。

**详略期望**：在 `format_h3.txt` overlay 中明确：`integrated_multimodal_description` 必须覆盖构图/主体/环境/道具/动作/镜头/声音，不能是剧情梗概。

### 3.4 ⑥ 质量校验（新增阶段）

**文件**：新增 `src/verify.py`、`prompts/verify_fix.txt`

**规则校验（确定性，无 LLM 调用）**：

| 校验项 | 规则 | 解决 |
|---|---|---|
| 字段存在与顺序 | 三字段（t2va/i2va/fl2va/l2va）/ 六段（r2va），顺序正确，字段名精确 | 结构不稳 |
| 关键帧对齐句 | 必须以官方前缀开头，S.SS 与时长一致（两位小数） | 结构不稳 |
| 时间戳单调 | `[Shot N] At 00:SS.mmm` 严格递增且在时长内 | 画面诡异 |
| 标签编号 | `<Picture/Video/Audio N>` 编号 ≤ 实际素材数 | 丢失参考素材 |
| 标签使用 | subject_definitions 定义的标签在正文被引用（r2va） | 丢失参考素材 |
| 语言标签 | `<d>[English]中文</d>` 语言标签与内容匹配 | 台词发音紊乱 |
| 画幅残留 | 无 aspect ratio / resolution / fps / 画幅词 | 结构不稳 |
| 意图锚点 | 最终提示词不偏离用户核心意图（规则层只查用户明确禁止词） | 意图偏差 |

**LLM 修复（可选，规则失败才触发）**：
- 新 `prompts/verify_fix.txt`：只修指定问题，不重写其他内容，最多 1 轮
- 修复后重新校验，仍失败则保留最后结果并标记 `verify_after_fix=failed`

**意图一致性 LLM 检查**：新增开关 `verify_intent_llm`（默认 `off`，可 `--verify-intent-llm` 开启）。开启时对原始意图 vs 最终提示词做一次 LLM 一致性检查（不新增剧情/不丢失要点）。

**decode 配置**：新增 `verify` 段（temperature 0.2）

## 4. 配置变更

`configs/gemini.yaml` 的 `decode` 增加：

```yaml
decode:
  perceive:
    temperature: 0.2
    top_p: 0.9
  expand:
    temperature: 0.7
    top_p: 0.95
  elaborate:
    temperature: 0.6
    top_p: 0.9
  format:
    temperature: 0.2
    top_p: 0.9
  verify:
    temperature: 0.2
    top_p: 0.9
```

顶层新增：
```yaml
verify:
  intent_llm: false       # 是否做 LLM 意图一致性检查（默认关）
  max_fix_rounds: 1       # 规则失败后的 LLM 修复轮数上限
```

## 5. 文件改动清单

### 新增
- `prompts/elaborate.txt` — 补细节阶段 SYSTEM
- `prompts/verify_fix.txt` — 校验失败修复 SYSTEM
- `prompts/verify_intent.txt` — LLM 意图一致性检查 SYSTEM
- `src/verify.py` — 规则校验 + LLM 修复编排
- `src/examples.py` — 官方完整输出范例（base-en Case 1~4 + ref-en Section 7）
- `scripts/compare_context_ir.py` — 官方 Context-IR 对照评估工具
- `test/test_verify.py` — 校验规则单测
- `docs/archive/2026-08-19-context-ir-quality-plan.md` — 实现计划

### 修改
- `prompts/perceive_image.txt` — 追加 What it can provide
- `prompts/perceive_refs.txt` — 追加 What it can provide
- `prompts/expand_intent.txt` — 明确写出声音设计
- `prompts/format_h3.txt` — 详略期望 + 语言标签规则强化
- `src/config.py` — `gemini_settings()` 解析 verify 配置
- `src/gemini.py` — 支持 verify 配置读取（已有 decode 机制，自动兼容）
- `src/skill.py` — `compose_format_system` 注入 few-shot 范例
- `src/pipeline.py` — 插入 elaborate / verify 两阶段
- `src/report.py` — 记录验证结果
- `scripts/run.py` — 新参数 `--verify-intent-llm`、`--no-verify`
- `configs/gemini.yaml.example` — 新 decode 段
- `README.md` — 更新管线说明
- `test/test_pipeline.py` — 补充新阶段测试（mock chat）

## 6. 验证闭环

1. `test/test_verify.py`：规则校验单测（时间戳/标签/语言标签/字段完整性/对齐句）
2. 现有 54 个测试保持通过
3. `scripts/compare_context_ir.py`：同一组意图+素材，分别用本 agent 和官方 API 出提示词，输出并排对比（token 数、结构完整性、差异 diff），用户后续分别推理出片做人工对比

## 7. 不在范围（YAGNI）

- 本地 H3 出片对接（用户明确不做）
- 完整五阶段 Context-IR 工作流（方案 B，被否决）
- 模型升级（方案 C 部分：仅支持通过配置换更强模型，不强制）
- 批量 / JSON 输出
- 视频并行对比（--compare-video 保持现状）
