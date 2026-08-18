# zwb 目录测试用例资产地图

梳理 `/kwkj-k8s/zwb/` 下与「agent 提示词增强 vs 官方 Context-IR 管线」相关的全部测试用例资产。
每个资产块给出：路径、用途、模式、有无官方/本地增强稿、有无成片，以及是否已纳入 `test/cases_agent_vs_official.py` 用例清单。

## 总览

| 资产根目录 | 内容 | 已纳入用例清单 |
| --- | --- | --- |
| `应用/Qwen提示词/母仓/` | 四模式基准 + r2va 基准 + 多参网格 + 双管线交付包 | ✅ |
| `应用/H3/Context-IR/` | 官方 IR API 测试 + Gemini 对照 + 行为探针 | ⚠️ 素材复用；探针清单见 §6 |
| `项目/R2VA_Qwen与官方对照/` | 官方 vs Qwen vs Agent 三路对照（01-05） | ✅ |
| `项目/FL2VA用例/` | 自建 FL2VA 全链路用例（棉花/吵架） | ✅ |
| `项目/LTX/` | LTX-2 管线用例（Gemma 增强/布达佩斯短片） | ❌ 不同模型管线 |
| `项目/Qwen打标/`、`项目/VBench评测/` | Qwen 打标提示词 / VBench 评测 | ❌ 评测工具，非 IR 对照 |

---

## 1. 母仓四模式基准（`应用/Qwen提示词/母仓/`）

`benchmarks/cases.yaml` 定义了 7 个用例（t2va / i2va / fl2va / l2va），
每个用例都有三份产物 + 两份成片：

| ID | 模式 | 短意图 | 官方增强稿 | 本地 Qwen 稿 | 官方成片 | Qwen 成片 |
| --- | --- | --- | --- | --- | --- | --- |
| wuxia_t2va | T2VA 9s | `benchmarks/set_01_wuxia/t2va_intent.txt` | `out/gold_ir/wuxia_t2va.txt` | `out/qwen_intent_v1.1/wuxia_t2va.txt` | `out/videos/gold_ir/wuxia_t2va.mp4` | `out/videos/qwen_intent_v1.1/wuxia_t2va.mp4` |
| wuxia_i2va | I2VA 9s | `set_01_wuxia/i2va_intent.txt` + first.png | `gold_ir/wuxia_i2va.txt` | `qwen_intent_v1.1/wuxia_i2va.txt` | ✅ | ✅ |
| wuxia_fl2va | FL2VA 9s | `set_01_wuxia/fl2va_intent.txt` + 首尾帧 | `gold_ir/wuxia_fl2va.txt` | ✅ | ✅ | ✅ |
| wuxia_l2va | L2VA 9s | `set_01_wuxia/l2va_intent.txt` + last.png | `gold_ir/wuxia_l2va.txt` | ✅ | ✅ | ✅ |
| neon_t2va | T2VA 6s | `set_02_neon_t2va/t2va_intent.txt` | `gold_ir/neon_t2va.txt` | ✅ | ✅ | ✅ |
| cotton_fl2va | FL2VA 8s | `set_03_cotton/fl2va_intent.txt` + 首尾帧 | `gold_ir/cotton_fl2va.txt` | ✅ | ✅ | ✅ |
| office_fl2va | FL2VA 6s | `set_04_office/fl2va_intent.txt` + 首尾帧 | `gold_ir/office_fl2va.txt` | ✅ | ✅ | ✅ |

**结论参考**：`母仓/deliverables/四模式双管线_飞书文档.md` 给出字数密度（Qwen/官方 ≈ 0.33~0.85）。

## 2. 母仓 r2va 基准（`out/gold_ir_r2va/` + `benchmarks/set_r2va_*`）

6 个 r2va 用例，官方稿在 `out/gold_ir_r2va/<id>.txt`，本地 Qwen 稿在 `out/qwen_intent_r2va_v1/<id>.txt`：

| ID | 输入 | 任务类型 |
| --- | --- | --- |
| char_action | 角色图 + 动作参考 mov | 角色做参考动作 |
| continuation | 尾帧 + 源视频 mp4 | 视频续写 |
| cont_keyframe | 尾帧 | 关键帧续写 |
| style_transfer | 粘土风格图 + 机械臂视频 | 风格迁移到动作 |
| style_new_plot | 风格图 + 风格视频 | 借鉴风格按新情节生成 |
| remove_overlay | 源视频（含水印/硬字幕） | 去水印/字幕并补全 |

另有 `set_r2va_multi_img`（时装 3 图，官方稿同时在 `gold_ir_r2va/multi_img.txt` 与对照项目 03）。

## 3. R2VA 官方 vs Qwen vs Agent 三路对照（`项目/R2VA_Qwen与官方对照/`）

| 目录 | 模式 | 素材 | 官方稿 | 其它对照稿 |
| --- | --- | --- | --- | --- |
| 01_风伯飙马 | r2va | 汉画像石拓片 4 图 | `提示词/official_r2va.txt` | qwen_baseline / denser / explore / anti_repeat 多稿 |
| 02_采莲水鸟 | r2va | 墨彩水鸟 4 图 | `提示词/official_r2va.txt` | 同上 |
| 03_时装多图 | r2va | 时装 3 图 | `提示词/official_r2va.txt` | `qwen_r2va.txt` + `omni_suit_denser_t01.txt` |
| 04_wuxia_t2va | t2va | 无 | `提示词/official_t2va.txt` | `agent_qwen38_v12.txt`（Agent 实测稿）+ omni_suit + qwen_denser |
| 05_neon_t2va | t2va | 无 | `提示词/official_t2va.txt` | 同上 |

根目录报告：
- `T2VA_Agent对照报告.md` —— 官方/Qwen3.6+西装/Agent 三路（镜数、密度、加戏编造分析）
- `T2VA_Omni对照报告.md`、`Omni对照报告.md` —— Omni 单轮对照
- `multiparam.yaml` —— 4 组解码参数网格定义（baseline/denser/explore/anti_repeat）

## 4. FL2VA 自建全链路用例（`项目/FL2VA用例/`）

每个用例按 `01_原始素材 → 02_处理后首尾帧 → 03_短意图 → 04_ContextIR增强 → 05_生成视频 → 06_日志` 流水线留档：

| 用例 | 首尾帧 | 短意图 | 官方增强稿 | 成片 |
| --- | --- | --- | --- | --- |
| 用例01_棉花领域模型 | 棉花开花 → NVIDIA 机房+棉株 | `03_短意图/fl2va_input_zh.txt` | `04_ContextIR增强/enhanced_en.txt` | `05_生成视频/fl2va_cotton.mp4` |
| 用例02_董事总经理吵架 | 双女高管对峙 → 短发愤怒特写 | 同上 | `04_ContextIR增强/enhanced_en.txt` | `05_生成视频/fl2va_quarrel.mp4` |

## 5. 特殊光影 T2VA 双管线对照（`母仓/out/special_light/`）

3 个详细短意图（光线物理/运镜/材质反光/禁止项），各跑官方 + 本地 Qwen：

| ID | 短意图 | 官方稿 | Qwen 稿 |
| --- | --- | --- | --- |
| light_backlight | `intents/backlight_逆光.txt` | `official/backlight_逆光.txt` | `qwen/backlight_逆光.txt` |
| light_neon | `intents/neon_霓虹.txt` | `official/neon_霓虹.txt` | `qwen/neon_霓虹.txt` |
| light_dusk | `intents/dusk_黄昏.txt` | `official/dusk_黄昏.txt` | `qwen/dusk_黄昏.txt` |

全文对照：`对照合并.md`；README 有字数密度（Q/官 ≈ 0.74~0.87）。

## 6. Context-IR 行为探针（`应用/H3/Context-IR/探针/`）

这些不是「对比用例」，而是**官方 IR 行为探测**（验证 agent 也应保持同样的行为边界）：

| 探针文件 | 用例 | 探测点 |
| --- | --- | --- |
| `cases.json` | TC-C01, TC-W01..Wxx | 联网/agent 行为：今日日期、抓 URL、实时天气 |
| `cases_r2va.json` | TC-R01..R06 | r2va 容量与解析：6 图收拢、8 图拉满、8图+3视频+3音频 |
| `cases_at_probe.json` | TC-AT01..AT08 | `@Picture` 编号策略：越界、颠倒、错模态、忽略全部 |
| `cases_func_probe.json` | TC-F01..F05 | 功能边界：错误命名纠偏、泛化词接地、字幕水印 |

探针素材：`探针/media/{imgs,vids,auds}`；结果：`results*`；测试报告：`测试报告.md`、`测试报告_r2va.md`。

## 7. 已纳入用例清单的映射

`test/cases_agent_vs_official.py` 的 `build_cases()` 现返回 **21 个用例**；加 `test_local_model_cases.py` 的 4 个本地素材用例，共 **25 个**。
所有用例的**输入素材**已集中复制到 **`runs/generated_media/cases/<case_id>/`**
（保留原文件名，测试自动重定位到该目录，缺失时回退 zwb 原路径）。

| 分组 | 用例 id |
| --- | --- |
| 母仓四模式（7） | wuxia_t2va / wuxia_i2va / wuxia_fl2va / wuxia_l2va / neon_t2va / cotton_fl2va / office_fl2va |
| R2VA 对照项目（3） | fengbo_r2va / cailian_r2va / r2va_multi_img |
| 母仓 r2va 基准（6） | char_action / continuation / cont_keyframe / style_transfer / style_new_plot / remove_overlay |
| FL2VA 自建（2） | fl2va_cotton_domain / fl2va_office_quarrel |
| 特殊光影（3） | light_backlight / light_neon / light_dusk |
| 本地模型生成素材（4，见 `test_local_model_cases.py`） | local_t2va_video_as_r2va_ref / local_i2va_firstframe / local_generated_video_as_ref / local_r2va_video_as_ref |

**模式支持矩阵**：

| 模式 | 官方 IR | 本地 Qwen | Agent | 说明 |
| --- | --- | --- | --- | --- |
| t2va | ✅ | ✅ | ✅ | agent 三字段对齐官方 |
| i2va | ✅ | ✅ | ✅ | agent 三字段 + 首帧对齐句 |
| fl2va | ✅ | ✅ | ❌ | 官方独有（首尾帧），agent 暂不覆盖 |
| l2va | ✅ | ✅ | ❌ | 官方独有（尾帧），agent 暂不覆盖 |
| r2va | ✅ | ✅ | ✅ | agent 六段对齐官方 |

## 8. 如何复用这些资产

```bash
# 1) prompt 层对比（全部用例结构校验，秒级，不调模型）
cd /kwkj-k8s/zwb/项目/agent/new_agent0818
pytest -q test/test_agent_vs_official.py

# 2) 本地 Qwen 重跑增强（复用母仓服务，非本 agent）
cd /kwkj-k8s/zwb/应用/Qwen提示词/母仓
bash scripts/run_local_enhance.sh --mode t2va --intent-file out/special_light/intents/neon_霓虹.txt --out-dir out/special_light/qwen

# 3) 官方 IR 重跑增强（Context-IR API）
python3 应用/H3/Context-IR/脚本/h3_context_ir.py -m t2va --prompt-file <短意图> --duration 9 --ratio 16:9 -o out.txt

# 4) 本地出片（agent 或用母仓脚本）
RUN_LOCAL_H3_MEDIA_TESTS=1 pytest -q test/test_local_h3_generation.py -k local_h3
```
