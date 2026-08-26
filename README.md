# ir_agent：MiniMax-H3 官方增强管线的替代 Agent

短意图先扩写、补细节，再按官方 [`h3-prompt-writing`](h3-prompt-writing/SKILL_en.md) skill 整理成 MiniMax-H3 Context-IR 字段，最后做一次质量校验；画幅、分辨率、时长只走视频 API，不写进 prompt。

仓库：<https://github.com/3CornSoups/ir_agent>

## 管线

感知（若需要）→ **风格 skill 路由**（可选）→ **T8 机制路由**（可选）→ 扩写 → **补细节** → 格式化（官方 `h3-prompt-writing` 指南 + 按需题材 overlay + 官方完整输出范例 few-shot）→ **质量校验**（规则硬校验 + 失败时自动 LLM 修复）。

- 感知阶段在事实库存末尾追加 `What it can provide`，区分「可复用主体」与「帧锚点」，供 `subject_definitions` 正确抽象 `<Subject>`；并强制写清人物/动物的 **clothing + body coverage**，避免库存阶段就丢衣服。
- 扩写阶段明确要求写出声音层（环境声 / 动作声 / 配乐器乐速度节奏）。
- 有参考库存时，扩写 / 补细节阶段施加 **VISUAL IDENTITY LOCK**：库存里的每个视觉属性（衣种、颜色、覆盖度、纹理、配饰、发型）必须等精度保留，**禁止省略 / 泛化 / 降覆盖**（不得把库存中穿好衣服的主体写成 nude / shirtless / 减少覆盖），补细节阶段还会把前一步已丢掉的属性**恢复回来**。
- 补细节阶段（`prompts/elaborate.txt`）**按场景复杂度自适应详略**：简单单镜头（4–5 秒、无切镜）写一个聚焦段落即可，不强制对齐官方长稿；多镜头 / 多节拍可以更长。同时禁止写入视频模型无法执行的物理精度（风速 m/s、克重 g/m²、精确焦距、毫米级位移等），改用动作级语言（「裙摆在风中飘动」，而不是「72g/m² 丝绸在 0.6m/s 侧风下形变」）。
- 格式化阶段注入官方完整输出范例（`src/examples.py`），稳定字段顺序与详略级别。
- 质量校验（`src/verify.py`）规则层免费：字段骨架与顺序、关键帧对齐句、时间戳单调、参考标签编号与使用、`<d>[语言]` 匹配、画幅残留；其中 r2va 的「标签使用」检查会把 `subject_definitions` 行首独立定义的 `<Subject N>` / `<Picture N>` / `<Video N>` / `<Audio N>` 全部纳入「必须被正文引用」；有 error 时自动让模型修一次（`prompts/verify_fix.txt`），修复后重新校验。

| 模式 | HTTP 次数（典型） | 步骤 |
| --- | --- | --- |
| t2va | 3–4 | 风格路由（可选）→ 扩写 → 补细节 → 格式化（官方 base 指南） |
| i2va | 4–5 | 看首帧 → 风格路由（可选）→ 扩写 → 补细节 → 格式化（附首帧） |
| fl2va | 4–5 | 看首尾帧 → 风格路由（可选）→ 扩写 → 补细节 → 格式化（附首尾帧） |
| l2va | 4–5 | 看尾帧 → 风格路由（可选）→ 扩写 → 补细节 → 格式化（附尾帧） |
| r2va | 4–5 | 看参考图/视频/音频 → 风格路由（可选）→ 扩写 → 补细节 → 格式化（官方 ref 指南） |

质量校验的 LLM 修复最多 1 次；开启 `--verify-intent-llm` 时会对「原始意图 vs 最终提示词」做一次 LLM 意图一致性检查（不新增剧情 / 不丢要点），且修复后会对修复稿**复检**，意图偏差结论不会被静默丢弃。两种 LLM 调用都失败 / 无用时不影响出 prompt，结果记入 `run.json` 与 report。

风格路由在 `hybrid` / `llm` 下：前置模型读 `skills/catalog.yaml` 短描述打分（0~1），取 top1；达 `llm_score_threshold` 才加载，**+1 次 HTTP**。`off` 不额外请求；`keyword` 为 `llm` 历史别名。

**T8 Creative DNA 机制**（扩写/补细节的因果节拍增强，与题材 skill 正交）默认 `hybrid`：中文标题/标签 **≥2 次命中**才走关键词；否则再问前置模型读 `skills/t8/catalog.yaml`（**+0~1 次 HTTP**）。机制 overlay 只注入扩写/补细节，不改 H3 字段骨架。上游：[T8mars/minimax-h3-prompt-skill-T8](https://github.com/T8mars/minimax-h3-prompt-skill-T8) **v1.1.8**（109 个稳定 selector）。同步命令：`python scripts/sync_t8_mechanisms.py`。

参考图若是四宫格 / 九宫格，感知会按格扫完并列出该 `<Picture>` 里出现的全部 `<Subject>`；漏格时自动再扫一次（多 1 次 HTTP）。

五模式最后一步共用 `prompts/format_h3.txt` + 官方指南，按 `MODE=` 切换输出骨架：

- t2va：三字段
- i2va / fl2va / l2va：官方对齐句 + 三字段（i2va 从首帧向前；fl2va 写首尾帧之间的路径；l2va 收敛到尾帧）
- r2va：六段（`subject_definitions` … `non_diegetic_music`）

### 官方 skill

`h3-prompt-writing/` 是 MiniMax H3 Prompt Writing 官方 skill 的本地副本：

| 文件 | 用途 |
| --- | --- |
| `SKILL_en.md` / `SKILL_cn.md` | 工作流：定模式 → 读对应指南 → 保留字段名/顺序/标签 |
| `references/base-en.txt` | T2VA / I2VA / FL2VA / L2VA 三字段写法 |
| `references/ref-en.txt` | Ref2VA 六段、`<Subject>` / `<Picture>` / `<Video>` / `<Audio>` |

中文 `base-cn.txt` / `ref-cn.txt` 只给人读；**运行时只注入英文指南**，避免 `[镜头 1]` 与官方英文骨架冲突。

Ref2VA 标签跟官方一致：上传顺序只用来编号源素材；人设/风格图不要单独建 `<Picture>` 行，写进对应 `<Subject>`；只有真正当首帧/关键帧/尾帧/分镜锚点的图才出独立 Picture 行。

### 动态风格 skill

前置路由在扩写之前按意图加载题材写法（**不**加载 Hub 出片工具或确认门）。底座始终是 `h3-prompt-writing`；风格 overlay 只补叙事/画风，字段名仍以官方指南为准。

写法压缩自 [MiniMax 官方 8 个题材 skill](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills)、[swan7-py/MiniMax-H3-Skills-Local](https://github.com/swan7-py/MiniMax-H3-Skills-Local) 与 [sjh00 分镜提示词技能集](https://github.com/sjh00/MiniMax-H3-Storyboard-Prompt-Generator-Skill)。非官方 / 社区 skill 也可接入：见 `skills/catalog.yaml` 顶部步骤与 `skills/overlays/_TEMPLATE_community.txt`（`origin: community` + `upstream`）。

| 路由 | 行为 |
| --- | --- |
| `hybrid`（默认） / `llm` | 前置模型对 catalog 短描述打分，取 top1；低于阈值则不选 |
| `keyword` | `llm` 的历史别名 |
| `off` | 不自动选；仍可用 `--skill` 强制加载 |

```bash
# 自动：品牌宣传意图会加载 brand-promo
python3 scripts/run.py -m t2va --intent "给产品拍一支品牌宣传片，结尾 CTA" --no-video

# 强制 3D 动画写法
python3 scripts/run.py -m t2va --intent "一只橘猫弹跳" --skill 3d-animation --skill-router off --no-video
```

可选 id：`brand-promo`、`minimalist-product-ad`、`3d-animation`、`papercraft-stop-motion`、`paper-collage`、`music-video-subtitle`、`co-op-game-intro`、`handdrawn-live`、`direct-street-interview`、`stage-startle-to-truce`。

### T8 Creative DNA 机制（基模增强）

在 **扩写 + 补细节** 阶段注入 T8 案例库的因果锚点（beat / proof / transition），提升叙事结构密度；**不**替代官方 `h3-prompt-writing` 格式化，也 **不**直接输出 T8 终稿 prompt。

| 路由 | 行为 |
| --- | --- |
| `hybrid`（默认） | 中文标题/标签 ≥2 命中则加载；否则前置模型返回 `{"mechanisms":[...]}` |
| `keyword` | 只靠触发词（保守，避免 slug 拆词误命中） |
| `llm` | 每次都问前置模型 |
| `off` | 不自动选；仍可用 `--mechanism` 强制加载 |

```bash
# 自动：产品证据递进类意图（关键词足够明确时）
python3 scripts/run.py -m t2va --intent "产品广告｜功能证据递进，先给结果再逐层证明" --no-video

# 强制指定机制（与 --skill 可叠加）
python3 scripts/run.py -m t2va --intent "深海潜水员在维修码头..." \
  --mechanism sensory-seal-location-swap-resumption --mechanism-router off --no-video
```

| 路径 | 用途 |
| --- | --- |
| `skills/t8/catalog.yaml` | 109 机制目录（id / 中文标题 / summary / triggers） |
| `skills/t8/overlays/*.txt` | 各机制的 Mandatory anchors overlay |
| `skills/t8/VERSION` | 上游版本 pin（当前 v1.1.8） |
| `prompts/route_mechanisms.txt` | 机制路由 SYSTEM |
| `src/mechanism_router.py` | 关键词 / LLM / 强制指定 |
| `scripts/sync_t8_mechanisms.py` | 从上游拉取/刷新目录与 overlay |

#### 本仓库目录

| 路径 | 用途 |
| --- | --- |
| `skills/catalog.yaml` | 风格 skill 目录：id、description、triggers、overlay 路径 |
| `skills/overlays/*.txt` | 各题材的写法 overlay（只进扩写/格式化，不改 H3 字段骨架） |
| `prompts/route_skills.txt` | 前置路由 SYSTEM：返回 `{"scores":{id:0~1}}`；agent 取 top1，低于 `llm_score_threshold` 则不选 |
| `src/skill_router.py` | LLM 量化打分 + 强制指定 |
| `src/skill.py` → `compose_format_system()` | overlay + 官方指南 + 风格 overlay 拼接 |
| `src/runlog.py` | 运行级模型调用日志：按 stage 成对落盘 request/response |
| `scripts/oneclick_run.py` | 服务器一键运行：增强 + 出片 + 每次运行一个 log 小目录 |

#### 参考仓库与本仓库的对应关系

写法与目录结构参考了以下开源仓库；**本仓库是「Gemini 扩写 + 官方 H3 格式化 + 可选 H3 出片」管线**，不是 WorkBuddy / MiniMax Hub 的逐步确认工作流。

| 参考仓库 | 借了什么 | 刻意没借什么（避免冲突） |
| --- | --- | --- |
| [MiniMax-AI/MiniMax-H3](https://github.com/MiniMax-AI/MiniMax-H3) `skills/h3-prompt-writing` | `h3-prompt-writing/references/base-en.txt`、`ref-en.txt`；字段名/对齐句/标签规则 | Hub 工具（`hub_generate_*`）、`agents/openai.yaml` 运行时依赖 |
| [MiniMax-AI/MiniMax-H3](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills) 八个题材 skill | 8 个题材的叙事/画风方法论，压缩为 `skills/overlays/` | 逐步确认门、`AskUserQuestion`、自动生图/剪辑/出片 |
| [swan7-py/MiniMax-H3-Skills-Local](https://github.com/swan7-py/MiniMax-H3-Skills-Local) | 「只产出提示词、不调用出片 API」的分层思路；题材 skill 与 `h3-prompt-writing` 解耦 | WorkBuddy 安装路径、交付物门、`image-prompt-writer-local` / `ace-step-1.5-prompt-writer`（未接入本管线） |
| [sjh00/MiniMax-H3-Storyboard-Prompt-Generator-Skill](https://github.com/sjh00/MiniMax-H3-Storyboard-Prompt-Generator-Skill) | 中文分镜 → 终稿字段的「先题材写法、再压三字段/六段」顺序 | 独立分镜 Markdown 交付物；运行时仍由本仓库统一格式化 |
| [T8mars/minimax-h3-prompt-skill-T8](https://github.com/T8mars/minimax-h3-prompt-skill-T8) @ v1.1.8 | 109 个 Creative DNA selector 的 summary + 结构锚点，压缩为 `skills/t8/overlays/` | Electron 桌面版、Seedance 伴侣 skill、ComfyUI 节点、API Workbench 出片流程 |

其它可参考但未直接 vend 的仓库：[joeVenner/awesome-minimax-h3](https://github.com/joeVenner/awesome-minimax-h3)（API 编排 skill）、[ComfyUI Wiki 官方 skill 说明](https://comfyui-wiki.com/en/news/2026-08-10-minimax-h3-official-skills)。

#### 已消解的提示词冲突

| 冲突点 | 旧行为 / 风险 | 当前行为 |
| --- | --- | --- |
| Ref2VA `<Picture>` | overlay 写「一个附件 = 一个 Picture」，与官方「人设图只进 Subject」矛盾 | `format_h3.txt` 对齐官方：仅首/关键/尾/分镜锚点才独立 Picture 行 |
| 中文指南注入 | `base-cn.txt` 含 `[镜头 1]`、部分对齐句中译，会与英文骨架冲突 | 运行时 **只** 注入 `base-en.txt` / `ref-en.txt`；中文文件仅供人读 |
| 风格 skill vs 官方字段 | 题材 skill 可能自带字段名或画幅 | 优先级：官方指南 > `format_h3.txt` overlay > 风格 overlay；overlay 内禁止改 MODE 骨架与画幅 |
| Hub 题材 skill 全文 | 含 `allowed-tools`、确认门、出片步骤 | 只提取「写法」段落，写入 `skills/overlays/`，不进入感知/路由 SYSTEM |

#### 官方题材 skill → 本仓库 overlay 映射

| 官方 / 分镜版 skill 名 | 本仓库 id | overlay 文件 |
| --- | --- | --- |
| `brand-promo-video-generator` | `brand-promo` | `skills/overlays/brand-promo.txt` |
| `minimalist-product-ad-generator` | `minimalist-product-ad` | `skills/overlays/minimalist-product-ad.txt` |
| `3d-animation-short-generator` | `3d-animation` | `skills/overlays/3d-animation.txt` |
| `papercraft-stop-motion-explainer` | `papercraft-stop-motion` | `skills/overlays/papercraft-stop-motion.txt` |
| `paper-collage-explainer-generator` | `paper-collage` | `skills/overlays/paper-collage.txt` |
| `music-video-subtitle-generator` | `music-video-subtitle` | `skills/overlays/music-video-subtitle.txt` |
| `co-op-game-intro-generator` | `co-op-game-intro` | `skills/overlays/co-op-game-intro.txt` |
| `handdrawn-live-video-generator` | `handdrawn-live` | `skills/overlays/handdrawn-live.txt` |
| [T8mars `direct-street-interview-video`](https://github.com/T8mars/minimax-h3-prompt-skill-T8/tree/main/skills/direct-street-interview-video) | `direct-street-interview` | `skills/overlays/direct-street-interview.txt` |
| [T8mars `stage-startle-to-truce-encounter`](https://github.com/T8mars/minimax-h3-prompt-skill-T8/tree/main/skills/stage-startle-to-truce-encounter) | `stage-startle-to-truce` | `skills/overlays/stage-startle-to-truce.txt` |

### 宫格 / 多主体

一张参考图可以是 2×2、3×3、分镜表。视觉模型容易只盯住最显眼的一格。感知阶段会：

1. 先报 `Layout: single scene | 2x2 grid | 3x3 grid | …`
2. 按阅读顺序写 `cell r,c`
3. 再列 **Subjects in this picture**（同一人跨格合并；不同人/物/场景拆开，都引用同一 `<Picture N>`）

官方规则：一个素材可以提供多个 Subject。不要把未读的格子丢掉。

## 配置

```bash
git clone https://github.com/3CornSoups/ir_agent.git
cd ir_agent
pip install -r requirements.txt

# 方式一：环境变量（推荐）
export GEMINI_API_KEY=...
export GEMINI_ENDPOINT=...          # Cloudsway 网关 endpoint
export MINIMAX_API_KEY=...          # 云端出片时需要；使用本地 MiniMaxH3 时可不填（见下）

# 方式二：复制示例配置
cp configs/gemini.yaml.example configs/gemini.yaml
cp configs/h3.yaml.example configs/h3.yaml
# 编辑上述 yaml 填入密钥（勿提交到 git）
```

仓库内 `configs/gemini.yaml` / `configs/h3.yaml` 的 `api_key` 为空；密钥只放本地或环境变量。

### Gemini 端点协议（protocol）

Cloudsway 网关对 Gemini 提供两套端点，多模态能力不同：

| protocol | 端点 | 多模态支持 |
| --- | --- | --- |
| `native`（默认） | `.../generateContent`（Gemini 原生协议） | 文本 / 图片 / **视频** / 音频 / PDF |
| `openai` | `.../google/chat/completions`（OpenAI 兼容层） | 仅文本 / 图片 |

- 配置项：`configs/gemini.yaml` 的 `protocol`（默认 `native`），或环境变量 `GEMINI_PROTOCOL`。
- 原生端点 URL 默认由 `endpoint` 推导为 `https://genaiapi.cloudsway.net/v1/ai/<endpoint>/generateContent`；
  也可用 `native_api_url` 或环境变量 `GEMINI_NATIVE_API_URL` 显式指定。
- 若用 `openai` 协议传入视频，上游会报 `Unrecognized 'type' field ... 'video_url'` 400 错误——
  因为 OpenAI 兼容层只接受 `image_url`。请勿在该协议下传视频。

### 视频大小限制与自动压缩

Gemini 原生端点的 `inlineData` 单次上限为 **20MB**。当参考视频超过该阈值时，agent 会自动用 **ffmpeg** 压缩兜底：

- 阈值：原始视频 > 20MB（`VIDEO_INLINE_LIMIT_BYTES`）。
- 压缩目标：≤ 15MB（`VIDEO_TARGET_BYTES`），为 base64 放大（约 4/3）留出余量。
- 压缩策略：先按原分辨率转码（`libx264 crf28 + aac 96k`）；仍超限则缩到 1280 宽再压一次。
- 产物固定转码为 `video/mp4`，仅作为**发送副本**，**不修改原始文件**。
- 依赖：`ffmpeg` 与 `ffprobe` 可执行文件（未安装时打印警告并按原样发送）。
- 该压缩只作用于 **Gemini 感知阶段**；MiniMax H3 出片用的参考视频**不做压缩**，保留原始质量。

## 本地 MiniMaxH3（可选）

如果你有一个本地的 MiniMax-H3 服务（HTTP 接口），且不需要鉴权，可以：

- 把 `configs/h3.yaml` 的 `base_url` 指向本地服务（例如 `http://127.0.0.1:xxxx`）
- 设置 `skip_auth: true`
- 或直接设置环境变量 `export H3_SKIP_AUTH=true`
- 如本地服务接口路径不是默认 `/v2/video_generation` / `/v2/query/video_generation/{task_id}`，可配置 `generate_path` / `query_path_template`

## 一键运行（服务器推荐）

`scripts/oneclick_run.py` 面向服务器：一条命令启动 agent 增强意图、输出增强结果，并且**每次运行在 `log/` 下生成一个小文件夹**，记录本次对模型的每一次 HTTP 请求与响应，便于排查问题。

```bash
# 单条意图，只增强不出片（默认）
python3 scripts/oneclick_run.py -m t2va --intent "一只橘猫在窗台晒太阳"

# 从文件读意图
python3 scripts/oneclick_run.py -m t2va --intent-file input.txt

# 批量（每行一条意图）
python3 scripts/oneclick_run.py -m t2va --intents-file intents.txt

# 出片（需要配置 configs/h3.yaml 或 MINIMAX_API_KEY）
python3 scripts/oneclick_run.py -m i2va --intent "人物向前走" \
  --first-frame first.png --duration 5 --video

# 关闭质量校验（更快）
python3 scripts/oneclick_run.py -m t2va --intent "..." --no-verify
```

每次运行生成两个目录（同名 `run_<模式>_<时间戳>_<序号>`）：

| 目录 | 内容 |
| --- | --- |
| `runs/run_<id>/` | 增强结果：`prompt.txt`、`expanded.txt`、`elaborated.txt`、`inventory.txt`、`run.json` |
| `log/run_<id>/` | 模型调用日志：按 stage 成对落盘 `01_expand_request.txt` / `01_expand_response.txt`、`02_perceive_*` …；`meta.json` 记录意图/模式/参数/结果路径 |

日志覆盖整条链路：风格/机制路由（`route`）、感知（`perceive`）、扩写（`expand`）、补细节（`elaborate`）、格式化（`format`）、校验修复（`verify`）、出片（`h3_create` / `h3_query`）。多模态请求里的 data URI 会自动截断，避免日志膨胀。`log/` 目录已被 `.gitignore` 忽略，不会误传服务器。

一键脚本参数与 `scripts/run.py` 基本一致（`--first-frame` / `--last-frame` / `--ref-image` / `--duration` / `--ratio` / `--resolution` / `--skill` / `--skill-router` / `--mechanism` / `--mechanism-router` / `--compare-video`），另有：

| 参数 | 说明 |
| --- | --- |
| `--video` | 增强后调用 H3 出片（默认只生成提示词，不出片） |
| `--no-verify` | 关闭提示词质量校验 |
| `--out-root DIR` | 增强结果输出根目录（默认 `runs/`） |
| `--log-root DIR` | 运行日志根目录（默认 `log/`） |

## 调用

```bash
# 只出 prompt
python3 scripts/run.py -m t2va --intent "一只橘猫在窗台晒太阳" --no-video

# t2va 出片（16:9 只进 API）
python3 scripts/run.py -m t2va --intent "雨夜涩谷，红巴士穿过路口，环境音，无对白" \
  --duration 6 --ratio 16:9 --out-dir runs/neon

# i2va
python3 scripts/run.py -m i2va --intent "人物向前走，镜头缓推" \
  --first-frame /path/to/first.png --duration 5 --no-video

# fl2va
python3 scripts/run.py -m fl2va --intent "从站立走到坐下" \
  --first-frame first.png --last-frame last.png --duration 8 --no-video

# l2va
python3 scripts/run.py -m l2va --intent "杯子从桌边滑落摔碎" \
  --last-frame last.png --duration 6 --no-video

# r2va（可附四宫格 / 九宫格人设图）
python3 scripts/run.py -m r2va --intent "保持人设，在街道上走路" \
  --ref-image face.png --ref-video walk.mp4 --duration 5 --no-video

# 风格 skill：自动量化打分路由（默认 hybrid）
python3 scripts/run.py -m t2va --intent "给产品拍一支品牌宣传片，结尾 CTA" --no-video

# 风格 skill：强制指定 + 关闭自动路由
python3 scripts/run.py -m t2va --intent "一只橘猫弹跳" \
  --skill 3d-animation --skill-router off --no-video

# 质量校验：关闭校验 / 开启 LLM 意图一致性检查
python3 scripts/run.py -m t2va --intent "一只橘猫在窗台晒太阳" --no-verify --no-video
python3 scripts/run.py -m t2va --intent "一只橘猫在窗台晒太阳" --verify-intent-llm --no-video
```

CLI 风格与校验相关参数：

| 参数 | 说明 |
| --- | --- |
| `--skill ID` | 强制加载风格 skill，可重复（最多 2 个，见 `catalog.yaml`） |
| `--skill-router MODE` | `hybrid`（默认）/ `llm` / `off`（`keyword`=`llm` 别名） |
| `--no-verify` | 关闭质量校验（含规则与自动修复） |
| `--verify-intent-llm` | 开启 LLM 意图一致性检查（对比原始意图与最终提示词，+1 次调用） |

## 可选：对照官方 Context-IR（`scripts/compare_context_ir.py`）

对同一组「意图 + 素材」，分别生成本地 agent 提示词与官方 H3-Context-IR 提示词，输出并排对照报告（结构校验 / 估算 token / 差异）：

- r2va 的结构校验会分别按 `--ref-image` / `--ref-video` / `--ref-audio` 的实际数量校验 `<Picture/Video/Audio N>` 编号，避免把合法引用误报为越界。

```bash
# 只生成本地提示词（无官方 key）
python3 scripts/compare_context_ir.py -m t2va --intent "一只橘猫在窗台晒太阳"

# 同时调用官方 API
python3 scripts/compare_context_ir.py -m r2va --intent "保持人设走路" \
  --ref-image face.png --ref-video walk.mp4 --official-key 您的key \
  --official-base-url https://api.minimaxi.com
```

输出在 `runs/compare_<时间>/`：`local_prompt.txt`、`official_prompt.txt`、`compare_report.md`、`compare.json`。之后可分别用本地 H3 与官方管线出片做人工质量对比。

## 可选：对比本地/官方提示词 + 视频（report 里会自动包含 diff）

如果你希望除了生成 `out_local.mp4` 之外，再基于 `official_prompt(raw)` 额外生成 `out_official.mp4`，可以加 `--compare-video`：

```bash
python3 scripts/run.py -m t2va --intent "一只橘猫在窗台晒太阳" --compare-video
```

输出在 `--out-dir`（默认 `runs/<mode>_<时间>/`）：

| 文件 | 内容 |
| --- | --- |
| `prompt.txt` | 本地优化后的最终字段（cleaned，可能含校验修复） |
| `prompt_official_raw.txt` | 官方/raw 提示词（未清洗） |
| `expanded.txt` | 第一次扩写稿 |
| `elaborated.txt` | 补细节后的场景散文 |
| `inventory.txt` | 关键帧 / r2va 的参考理解（宫格会含 Layout 与各格 Subject） |
| `run.json` | 元数据（含 `style_skills`、`style_skill_source`、`verify` 校验结果） |
| `out_local.mp4` | 成片（未加 `--no-video` 时，基于 local prompt） |
| `out_official.mp4` | （可选）成片（加 `--compare-video` 时，基于 official/raw prompt） |
| `report.json` / `report.md` / `prompt_diff.txt` | 提示词对比报告（总是生成） |

出片走 MiniMax `/v2/video_generation`；画幅 `--ratio`、分辨率 `--resolution` 只进视频 API。

## 测试（本地离线）

```bash
pip install -r test/requirements.txt
./test/run_tests.sh

# 风格路由与 R2VA Picture 规则
pytest -q test/test_skill_router.py test/test_pipeline.py

# 质量校验规则
pytest -q test/test_verify.py

# 运行级模型调用日志（不调用任何模型）
pytest -q test/test_runlog.py
```

默认不跑本地 MiniMax-H3 出片。需要时：

```bash
RUN_LOCAL_H3_MEDIA_TESTS=1 pytest -q test/test_local_h3_generation.py -k local_h3
```
