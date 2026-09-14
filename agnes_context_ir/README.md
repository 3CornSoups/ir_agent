# agnes_context_ir — 提示词增强说明

把用户的**短意图**（以及可选参考图/视频/音频）增强成 **MiniMax-H3** 可直接投片的结构化提示词。

入口：`src.pipeline.enhance`（产线侧常挂在 `app.vendor.agnes_context_ir`）。

启用方式：

```bash
AGNES_PROMPT_ENHANCE_ENABLED=true
AGNES_PROMPT_ENHANCE_BACKEND=agnes_context_ir
```

---

## 一句话理解

**先理解意图与素材，再写成 H3 官方格式。**  
不做风格 skills / T8 机制路由；不额外打 LLM 校验轮。终轮只做本地清洗（去前缀、补对齐句、清画幅泄漏）。

---

## 整体数据流

```
短意图 (+ 关键帧图 / 多参考素材)
        │
        ▼
   Gemini 多轮编排（2 或 3 轮）
        │
        ▼
   MiniMax-H3 结构化 prompt
        │
        ▼
   本地后处理 → 最终 prompt（可直接出片）
```

| 模式 | 含义 | 轮数 | 终轮字段形态 |
|------|------|------|--------------|
| **t2va** | 纯文生视频 | 2 | 三字段（描述 / 音景 / 配乐） |
| **i2va** | 首帧图生视频 | 2 | 对齐句 + 三字段 |
| **fl2va** | 首尾帧生视频 | 2 | 对齐句 + 三字段 |
| **l2va** | 尾帧生视频 | 2 | 对齐句 + 三字段 |
| **r2va** | 多参考（图/视频/音频） | 3 | 六段式（主体定义 → … → 配乐） |

时长默认从意图里「约 N 秒」解析，夹到 **4–15** 秒。

---

## 各模式怎么跑

### 1. t2va（纯文本，2 轮）

```
R1 keyinfo  →  R2 format
（文本模型）     （文本模型 + 官方 base 指南）
```

| 轮次 | 提示词文件 | 干什么 |
|------|------------|--------|
| R1 | `prompts/r1_keyinfo.txt` | 抽出 `[USER CONSTRAINTS]`（锁定禁项/台词/运镜等）+ `[POLISHED INTENT]`（英文场景散文） |
| R2 | `prompts/r2_format.txt` + `_core` + `h3-prompt-writing` base 指南 | 序列化成 H3 三字段，**不再发明新对白/新剧情** |

### 2. i2va / fl2va / l2va（关键帧，2 轮）

```
R1 image_guide（看图）  →  R2 format（文本模型 + 官方指南）
```

| 轮次 | 提示词文件 | 干什么 |
|------|------------|--------|
| R1 | `prompts/r1_image_guide.txt` | 约束锁定 + 画面事实 + 如何从关键帧展开/衔接/落幅 |
| R2 | 同 t2va 的 `r2_format` | 写成带**对齐句**的 H3 三字段 |

关键帧语义：

- **i2va**：`<Picture 1>` = 0.00s 首帧，往后演
- **fl2va**：图1=开头，图2=结尾，中间补运动路径
- **l2va**：图1=片尾落点，往前推如何到达

### 3. r2va（多参考，3 轮）

```
R1 perceive_fuse（看素材）  →  R2 plot（情节）  →  R3 format（六段式）
```

| 轮次 | 提示词文件 | 干什么 |
|------|------------|--------|
| R1 | `prompts/r1_perceive_fuse.txt` | 资产清单、关键主体 `<Subject N>`、与意图融合的 `[FUSED PROMPT]` |
| R2 | `prompts/r2_plot.txt` | 完整 `[SCENE NOTE]`（分镜、出场顺序、台词归属） |
| R3 | `prompts/r3_format_r2va.txt` + `_core` + ref 指南 | 写成六段：`subject_definitions` → `summary` → `retention_analysis` → `detailed_description` → `overall_soundscape` → `non_diegetic_music` |

素材上限：图 ≤ 9，视频 ≤ 3，音频 ≤ 3；至少提供一类。

---

## 核心逻辑：保真优先、格式轮不发明

格式轮（R2 format / R3 format）的 SYSTEM 会前置 `prompts/_core.txt`，再拼官方写作指南。优先级：

**用户意图 + 上游轮次产出 ≫ 官方指南里的示例**

几条硬规则（写进 `_core`，各规划轮也会呼应）：

1. **保真**：禁项、原文台词、动作顺序、锁定运镜词，下游不得删改翻译。
2. **只 enrichment**：可补光影/材质/氛围；不新增大角色、字幕、剧情节拍。
3. **对白策略**
   - 用户禁言（不要说话 / 无对白 / silent…）→ 标记 `dialogue=forbidden`，全程不出现 `<d>`
   - 已有具体台词 → 原样进 `<d>[Language]…</d>`（中文用 `[Chinese]`，不用 Mandarin）
   - 对话场景但缺台词 → **仅规划轮**可补 1～2 句短台词；格式轮禁止新编
   - 纯动作/空景 → 不要对白
4. **运镜策略**：用户指定的运镜原样保留；未指定则默认**静止或轻微缓推**，禁止炫技甩镜/乱切。
5. **制作参数**（画幅、分辨率、fps）只走 API，**不写进** H3 字段。

---

## 本地后处理（无 LLM）

`clean_final_prompt`（`src/postprocess.py`）在终轮之后：

1. 去掉误输出的 `MODE=...`、markdown 代码围栏
2. 关键帧模式：规范/补齐对齐句（首帧 0.00s、尾帧对齐到时长等）
3. 清掉误写入正文的画幅/分辨率/帧率字样

旧接口里的 `enable_verify` 仍接受，但**不再**做 LLM 修复轮，只保留本地清洗。

---

## SYSTEM 怎么拼出来

```
格式轮 SYSTEM =
    _core.txt（分层：语法官方优先 / 内容用户锁定）
  + 阶段提示词（r2_format / r3_format_r2va）
  + --- H3 syntax skills (binding) ---
  + prompts/skills/ 按模式装配的规则卡
  + --- Official example (format reference only) ---
  + prompts/examples/<mode>.txt（单个官方 Case）

规划轮 SYSTEM =
    _core.txt
  + 阶段提示词（r1_* / r2_plot）
  + --- H3 syntax skills (planning subset) ---
  + CAST / SHOT PLAN /（r2va）TASK TYPE / RETENTION PLAN 等规则卡
```

官方 `h3-prompt-writing/references/base-en.txt` / `ref-en.txt` 仍保留为溯源底本，**默认不再整段注入**格式轮。

终轮后 `verify_local` 做零成本语法验收（字段、镜头时间、`(Sx)`、retention 标记等），结果写入 `run.json` 的 `verify`，只告警不改稿。

---

## 返回什么

`enhance(...)` 主要字段：

| 字段 | 含义 |
|------|------|
| `prompt` | 清洗后的最终稿（推荐投片） |
| `prompt_official` / `prompt_raw` | 模型终轮原文 |
| `rounds` / `steps` | 每轮输出，便于排查 |
| `inventory` / `expanded` / `elaborated` | 兼容旧字段：感知稿 / 规划稿 / 终稿 |

指定 `out_dir` 时会写出 `prompt.txt`、`round_N.txt`、`run.json` 等。

`run_job` = `enhance` + 可选调用 H3 出片。

兼容占位（接受但忽略）：`skills` / `skill_router` / `mechanisms` / `mechanism_router`。

---

## 目录速览

```
agnes_context_ir/
├── src/
│   ├── pipeline.py      # enhance / run_job 编排
│   ├── prompts.py       # 各轮 USER 消息、SYSTEM 装配（skills + example）
│   ├── postprocess.py   # 终轮本地清洗 + verify_local
│   ├── gemini.py        # 模型调用
│   └── config.py        # 环境变量 + SKILL_SETS
├── prompts/
│   ├── _core.txt / r*.txt
│   ├── skills/          # H3 语法规则卡（按模式装配）
│   └── examples/        # 每模式 1 个官方 Case
├── h3-prompt-writing/   # MiniMax 官方写作指南（溯源底本）
├── tools/build_viewer.py
└── tests/
```

---

## 常用环境变量

| 变量 | 作用 |
|------|------|
| `AGNES_GEMINI_API_KEY` / `AGNES_GEMINI_ENDPOINT` / `AGNES_GEMINI_MODEL` | Gemini 凭证与默认多模态模型 |
| `AGNES_GEMINI_TEXT_MODEL` | t2va / 纯文本轮模型（未设则回退 `AGNES_GEMINI_MODEL`） |
| `AGNES_GEMINI_ENABLE_THINKING` | 思考模式，默认关闭 |
| `AGNES_CONTEXT_IR_GEMINI_PROTOCOL` | `native` 或 `openai` |

无 YAML；全部环境变量见 `src/config.py`。

改 vendor 副本后跑：`tests/test_engineering_fixes.py`。
