# Agent vs 官方 Context-IR 对比测试用例

> **完整 zwb 资产地图见 [test_cases_inventory.md](test_cases_inventory.md)**（母仓四模式 / r2va 基准 / R2VA 三路对照 / FL2VA 自建 / 特殊光影 / 行为探针）。

本目录提供一套用于对比「Agent 提示词增强管线」与「官方 Context-IR 管线」的测试用例。
用例素材分两类来源：**zwb 现成资产**（官方 IR 输出已留档）与**本地文生图/文生视频模型生成**。

## 一、对比的是什么

同一意图 + 同一输入素材，两条管线分别产出视频提示词：

| 管线 | 输入 | 产出 |
| --- | --- | --- |
| 官方 Context-IR | 短意图 + 参考媒体 | `official_*.txt`（t2va 三字段 / r2va 六段） |
| Agent | 短意图 + 参考媒体 | `enhance()` 产物（相同骨架，额外保证无画幅/分辨率残留） |

对比维度：
- **骨架一致性**：两者字段名/顺序是否与 `prompts/format_h3.txt` 规定的骨架一致
- **画幅残留**：官方 IR 输出可能把 `16:9`、`768P` 写进 prompt，Agent 的 `strip_canvas` 承诺清掉
- **引用合法性**：r2va 的 `subject_definitions` 是否只引用输入里真实存在的 `<Picture N>` / `<Video N>`
- **出片差异**（可选）：同一 prompt 喂本地 MiniMax-H3，对比成片

## 二、文件说明

| 文件 | 作用 |
| --- | --- |
| `cases_agent_vs_official.py` | 用例清单（数据驱动，现 21 个用例）。素材路径自动重定位到 `runs/generated_media/cases/<case_id>/` |
| `test_agent_vs_official.py` | prompt 层对比测试：素材存在性 → agent 结构（mock chat）→ 官方输出骨架/帧对齐句 → 本地 Qwen 稿结构 → 成片存在性 |
| `test_local_model_cases.py` | 用 `runs/generated_media/` 下本地模型生成的素材构造 i2va/r2va 用例 |
| `test_local_h3_generation.py` | 可选重型测试：调用本地 MiniMax-H3 脚本实际出片（默认 skip） |
| `test_cases_inventory.md` | **完整 zwb 测试用例资产地图**（母仓/Context-IR/对照项目/FL2VA/探针） |

所有用例输入素材已集中到 **`runs/generated_media/cases/<case_id>/`**
（详见该目录 `README.md`）；`test_cases.md` 下表里的素材位置均指复制后的文件。

## 三、用例清单（25 个）

### 母仓四模式基准（官方稿 + 本地 Qwen 稿 + 双成片齐全）

| id | 模式 | 官方 IR 输出 |
| --- | --- | --- |
| `wuxia_t2va` | T2VA 9s | `母仓/out/gold_ir/wuxia_t2va.txt`（对照项目另有 agent 实测稿） |
| `wuxia_i2va` | I2VA 9s | `gold_ir/wuxia_i2va.txt` |
| `wuxia_fl2va` | FL2VA 9s | `gold_ir/wuxia_fl2va.txt` |
| `wuxia_l2va` | L2VA 9s | `gold_ir/wuxia_l2va.txt` |
| `neon_t2va` | T2VA 6s | `gold_ir/neon_t2va.txt`（对照项目另有 agent 实测稿） |
| `cotton_fl2va` | FL2VA 8s | `gold_ir/cotton_fl2va.txt` |
| `office_fl2va` | FL2VA 6s | `gold_ir/office_fl2va.txt` |

### R2VA 对照项目（官方六段稿 + 素材）

| id | 意图 | 输入 | 官方 IR 输出 |
| --- | --- | --- |
| `fengbo_r2va` | 汉画像石拓片四图（风伯飙马） | 4 图 | `01_风伯飙马/提示词/official_r2va.txt` |
| `cailian_r2va` | 墨彩水鸟四图（采莲水鸟） | 4 图 | `02_采莲水鸟/提示词/official_r2va.txt` |
| `r2va_multi_img` | 时装多图（金标）人物/眼镜/服装一致 | 3 图 | `gold_ir_r2va/multi_img.txt` + `03_时装多图/提示词/official_r2va.txt` |

### 母仓 r2va 基准（官方稿在 `gold_ir_r2va/`）

| id | 输入 | 任务类型 |
| --- | --- | --- |
| `char_action` | 角色图 + 动作 mov | 角色做参考动作 |
| `continuation` | 尾帧 + 源视频 | 视频续写 |
| `cont_keyframe` | 尾帧 | 关键帧续写 |
| `style_transfer` | 风格图 + 机械臂视频 | 风格迁移到动作 |
| `style_new_plot` | 风格图 + 风格视频 | 借鉴风格按新情节生成 |
| `remove_overlay` | 源视频（水印/硬字幕） | 去水印并补全 |

### FL2VA 自建用例（官方全链路留档）

| id | 用例 |
| --- | --- |
| `fl2va_cotton_domain` | 棉花领域模型（棉花开花 → 机房终态） |
| `fl2va_office_quarrel` | 董事总经理吵架（对峙 → 愤怒离场） |

### 特殊光影 T2VA（官方 + 本地 Qwen 双稿）

| id | 主题 |
| --- | --- |
| `light_backlight` / `light_neon` / `light_dusk` | 逆光 / 霓虹 / 黄昏 |

### 本地模型生成素材（见 `test_local_model_cases.py`）

| id | 用途 |
| --- | --- |
| `local_t2va_video_as_r2va_ref` | 本地 t2va 成片作 r2va 动作参考 |
| `local_i2va_firstframe` | 本地生成图作 i2va 首帧 |
| `local_generated_video_as_ref` | 本地 i2va 成片作参考视频 |
| `local_r2va_video_as_ref` | 本地 r2va 成片（含音频）作参考 |

## 四、运行方式

### 1. prompt 层对比（不调用任何模型，秒级）

```bash
cd /kwkj-k8s/zwb/项目/agent/new_agent0818
pytest -q test/test_agent_vs_official.py test/test_local_model_cases.py
```

期望：全部通过；`-rs` 可看是否有 skip（本地素材缺失）。

### 2. 用本地模型生成素材（可选，需 GPU 约 30 分钟）

```bash
# 生成 t2va/i2va/r2va 参考素材到 runs/generated_media/
RUN_LOCAL_H3_MEDIA_TESTS=1 pytest -q test/test_local_h3_generation.py -k local_h3
```

GPU 选择可通过 `LOCAL_H3_T2VA_GPUS` / `LOCAL_H3_FL2VA_GPUS` / `LOCAL_H3_REF2VA_GPUS` 覆盖
（默认避开常驻的 0-3，用 5/6、6/7）。显存不足会自动 skip 而不是失败。

### 3. 出片层对比（agent vs 官方）

对同一用例：
- 官方侧：把 `official_*.txt` / `gold_ir/*.txt` 作为 prompt 喂本地 `gen_t2va_fast.sh` / `gen_ref2va.sh`
- agent 侧：跑 `enhance()` 拿到 prompt，同样喂本地脚本
- 对比成片（可人工看，或后续加帧级/画质指标）

## 五、如何新增用例

在 `cases_agent_vs_official.py` 的 `build_cases()` 里加一条 dict：

```python
{
    "id": "my_new_case",
    "mode": "r2va",                       # t2va / i2va / fl2va / l2va / r2va
    "source": "zwb-asset",                # 或 "local-model"
    "intent": "……意图文本……",
    "reference_images": [str(某图路径)],
    "reference_videos": [str(某视频路径)],
    "duration": 5,
    "ratio": "16:9",
    "official_prompt": str(官方输出路径),   # 没有就 None
    "note": "说明",
}
```

素材路径必须真实存在，`test_case_asset_files_exist` 会自动校验。
fl2va 需提供 `first_frame` 与 `last_frame`；l2va 需提供 `last_frame`。
