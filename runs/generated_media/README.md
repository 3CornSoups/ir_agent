# runs/generated_media —— 测试用例素材库

本目录集中存放 agent vs 官方 Context-IR 对比测试所需的全部素材。

## 目录结构

```
generated_media/
├── cases/                        # 21 个 zwb 资产用例的输入素材（按用例 id 分目录）
│   ├── <case_id>/                # 母仓四模式 / R2VA 对照 / r2va 基准 / FL2VA 自建
│   │   ├── first.png             # i2va/fl2va 首帧
│   │   ├── last.png              # fl2va/l2va 尾帧
│   │   ├── ref*.jpg|png          # r2va 参考图
│   │   ├── *.mp4|*.mov           # r2va 参考视频 / 源视频
│   └── ...
├── i2va_参考首帧.png              # 本地模型生成：i2va 首帧
├── i2va_测试参考.mp4              # 本地模型生成：i2va 参考视频
├── r2va_参考图.png                # 本地模型生成：r2va 参考图
├── r2va_测试参考.mp4              # 本地模型生成：r2va 参考视频（含音频）
└── t2va_测试参考.mp4              # 本地模型生成：t2va 参考视频
```

## 素材来源

| 来源 | 位置 |
| --- | --- |
| zwb 资产（母仓/对照项目/FL2VA） | 复制到 `cases/<case_id>/`，保留原文件名 |
| 本地模型生成 | MiniMax-H3 本地出片，根目录直放 |

用例清单 `test/cases_agent_vs_official.py` 会把素材路径自动重定位到
`cases/<case_id>/`；若某文件缺失则回退到 zwb 原路径。

## 无素材用例

以下用例无需输入素材（纯文本 t2va），`cases/` 下对应目录为空：

- `wuxia_t2va` / `neon_t2va`（母仓四模式 t2va）
- `light_backlight` / `light_neon` / `light_dusk`（特殊光影 t2va）

## 与官方输出的关系

`cases/<case_id>/` 只放**输入素材**。官方增强稿、本地 Qwen 稿、双管线成片仍在 zwb
资产原位（母仓 `out/gold_ir/`、`out/videos/` 等），用例清单里的
`official_prompt` / `local_qwen_prompt` / `official_video` / `local_qwen_video`
字段直接指向它们。
