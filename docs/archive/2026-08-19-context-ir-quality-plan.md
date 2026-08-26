# 提示词质量增强管线 Implementation Plan

> **执行说明：** 本计划按 superpowers:executing-plans 在本会话内联执行，用户已确认「写成 spec 文档开始实现」。此文档是任务分解与验收记录，代码已落地。

**Goal:** 在现有 5 步增强管线上补「补细节 + 质量校验」两阶段，并增强感知/扩写/格式化，使本地生成的 H3 提示词在详略度、参考素材利用、结构稳定性、声音设计上对齐官方 Context-IR。

**Architecture:** 感知→风格路由→扩写→补细节(新)→格式化(few-shot)→质量校验(新)。校验层为确定性规则（免费），存在 error 时自动 LLM 修复一轮；意图一致性 LLM 检查为可选开关。

**Tech Stack:** Python 3.11，`requests`/`pyyaml`，pytest。

## Global Constraints

- 只输出提示词；不负责本地 H3 出片对接。
- 现有 CLI / 输出目录 / report 结构保持兼容。
- 全部函数级注释用中文。
- 所有新规则在 `test/test_verify.py` 有单测；既有 54+ 个测试保持通过。
- 新增 stage 名固定为：`elaborate` / `verify` / `verify_intent`（chat stage 参数）。

---

## Task 1: 感知 prompt 升级（What it can provide）

**Files:**
- Modify: `prompts/perceive_image.txt`
- Modify: `prompts/perceive_refs.txt`

**Interfaces:**
- Produces: 感知输出在每项素材后追加 `What it can provide:` 小节，供 `_format_user` 的 inventory 直接透传。

- [x] `perceive_image.txt`：追加「What it can provide」说明与示例维度（身份外观/服装/光照/环境/动作/帧锚点/分镜）。
- [x] `perceive_refs.txt`：追加同类段落，并区分「可复用主体」vs「帧锚点」vs「待剪辑/续写源视频」。
- [x] 验证：`test_perceive_prompts_cover_grid_subjects` 仍通过（保留 `Subjects in this picture`、`3x3`、`4-grid` 等既有关键词）。

## Task 2: 扩写 + 格式化 prompt 增强

**Files:**
- Modify: `prompts/expand_intent.txt`
- Modify: `prompts/format_h3.txt`

**Interfaces:**
- Produces: 扩写输出强制包含声音层；格式化 overlay 新增详略期望与语言标签硬规则。

- [x] `expand_intent.txt`：增加「Always include a sound layer」要求（环境声/动作声/配乐器乐速度节奏）。
- [x] `format_h3.txt`：Shared constraints 增加 `integrated_multimodal_description` 详略期望（构图/主体/环境/道具/动作/镜头/声音，不是剧情梗概）；`<d>[语言]` 必须与内容实际文字匹配。

## Task 3: 补细节阶段（elaborate）

**Files:**
- Create: `prompts/elaborate.txt`
- Modify: `src/pipeline.py`

**Interfaces:**
- Consumes: `expanded`（扩写稿）、`inventory`（可选）
- Produces: `_elaborate_user(expanded, inventory)` → str；`record["elaborated"]`；`out_dir/elaborated.txt`

- [x] 新建 `prompts/elaborate.txt`：视觉/光/镜头/声音/音乐五个维度的补全要求 + 硬约束（不新增剧情/角色/结局、不发明素材、不写字段名）。
- [x] `pipeline.py` 在 expand 之后插入 elaborate 调用（stage="elaborate"）。
- [x] `record["elaborated"]` + `elaborated.txt` 落盘。
- [x] decode 配置 `elaborate: {temperature: 0.6, top_p: 0.9}`。

## Task 4: 格式化 few-shot（官方范例注入）

**Files:**
- Create: `src/examples.py`
- Modify: `src/skill.py`

**Interfaces:**
- Produces: `official_example(mode) -> str | None`；`compose_format_system` 在官方指南后追加 `--- Example output (official, complete) ---`。

- [x] `src/examples.py`：按 mode 收录官方完整范例（t2va/i2va/fl2va/l2va 来自 base-en Case 1~4，r2va 来自 ref-en Section 7）。
- [x] `compose_format_system`：匹配模式时注入对应范例。
- [x] 验证：`test_compose_format_system_injects_official_guide` 仍通过。

## Task 5: 质量校验阶段（verify）

**Files:**
- Create: `src/verify.py`
- Create: `prompts/verify_fix.txt`
- Create: `prompts/verify_intent.txt`
- Modify: `src/pipeline.py`

**Interfaces:**
- Consumes: `chat(system, user, *, stage=...)`（与 pipeline 相同签名）
- Produces: `verify_prompt(mode, prompt, *, duration, images, videos, audios) -> list[VerifyIssue]`；`verify_and_fix(...) -> dict{prompt, fixed, status, rounds, issues, errors, warnings, intent_llm}`

- [x] 规则校验：字段骨架与顺序 / 对齐句前缀与 S.SS / 时间戳严格递增且不超时长 / 标签编号 ≤ 素材数 / r2va 行首定义标签须被正文引用 / `<d>[语言]` 内容匹配 / 画幅残留。
- [x] LLM 修复：`verify_fix.txt` 只修指定问题，最多 `max_fix_rounds` 轮，修复后重新校验；模型无改动时防死循环。
- [x] 意图一致性检查：`verify_intent.txt`，返回 JSON，失败降级为 warning。
- [x] `pipeline.py` 在 format 之后调用 `verify_and_fix`，修复结果覆盖 `prompt` 并追加 `verify_fix` step；`record["verify"]`。

## Task 6: 配置与 CLI

**Files:**
- Modify: `src/config.py`
- Modify: `configs/gemini.yaml.example`
- Modify: `configs/gemini.yaml`（用户本机配置）
- Modify: `scripts/run.py`
- Create: `scripts/compare_context_ir.py`

**Interfaces:**
- Produces: `gemini_settings()["verify"] = {intent_llm: bool, max_fix_rounds: int}`；`enhance(..., enable_verify=True, verify_intent_llm=None)`；`run_job` 透传。

- [x] `config.py`：解析顶层 `verify:` 配置。
- [x] yaml：`decode` 增 `elaborate`/`verify`；顶层 `verify: {intent_llm: false, max_fix_rounds: 1}`。
- [x] `run.py`：`--no-verify`、`--verify-intent-llm`；打印校验状态。
- [x] `compare_context_ir.py`：本地 enhance + 可选官方 H3-Context-IR API 调用，输出 `compare_report.md`/`compare.json`。

## Task 7: 报告

**Files:**
- Modify: `src/report.py`

**Interfaces:**
- Consumes: `record["verify"]`

- [x] `report.json` 增加 `verify` 段；`report.md` 增加「质量校验」小节（status/errors/warnings/fixed/issues）。

## Task 8: 测试

**Files:**
- Create: `test/test_verify.py`
- Modify: `test/test_pipeline.py`
- Modify: `test/test_agent_vs_official.py`

- [x] `test_verify.py`：15 个用例覆盖全部规则 + `verify_and_fix` 修复/不修复路径。
- [x] `test_pipeline.py`：全部 mock 增加 elaborate 分支；steps 断言更新；format mock 改为合法 H3 字段（避免校验误触发修复）。
- [x] `test_agent_vs_official.py`：mock 增加 elaborate 分支（服务端运行）。
- [x] 全量：`pytest -q test/`（排除依赖 `/kwkj-k8s` 素材的 `test_agent_vs_official.py`）通过。

## Task 9: 文档

**Files:**
- Modify: `README.md`
- Modify: `docs/archive/2026-08-19-context-ir-quality-design.md`

- [x] README：管线说明（6 步 + 校验表）、HTTP 次数表、CLI 新参数、对照脚本用法、输出文件清单。
- [x] spec：文件清单补充 `verify_intent.txt` 与 `examples.py`。

## 验收结果

- 本机全量测试：`sss................................................................`（全部通过，3 个 skip 为本地 H3 出片用例）。
- 冒烟测试：stages=`expand,elaborate,format`，verify_status=`passed`，`elaborated.txt` 落盘，画幅词剥离后字段结构完好。
- `python scripts/run.py --help`：新参数正常显示。
