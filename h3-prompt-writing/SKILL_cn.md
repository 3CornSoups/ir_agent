---
名称：h3-prompt-writing
描述：针对 T2VA、I2VA、FL2VA、L2VA 和 Ref2VA 编写 MiniMax H3 视频生成提示词。这些提示词适用于以下场景：将多模态请求重写为 H3 提示词结构；撰写综合多模态描述（integrated_multimodal_description）、整体声景（overall_soundscape）及非叙事性音乐（non_diegetic_music）；对齐关键帧；或定义图像、视频及音频的参考标签。
兼容性：可移植至任何能够读取本地文件的智能体——无需外部 API 调用、MiniMax Hub 工具或专有运行时。`agents/openai.yaml` 文件仅包含可选的 ChatGPT/Codex UI 元数据，并不会将该技能局限于 OpenAI 智能体。
---

# H3 Prompt Writing

## 工作流程

1. 确定输入模式：T2VA、I2VA、FL2VA、L2VA 或全参考模式 Ref2VA。
2. 若为基础文本/关键帧模式，请参阅 `references/base-en.txt` 并遵循其最终提示词结构。
3. 若为全参考模式，请参阅 `references/ref-en.txt` 并遵循其六段式重写格式。
4. 请严格保留所选指南中的字段名称、章节顺序、标签及时间标记格式。

## 基础模式(Base Modes)

- T2VA：根据文本构建完整的视听时间轴。
- I2VA：从首帧开始，并基于此向后发展。
- FL2VA：描述首帧与末帧之间的连续演变路径。
- L2VA：推断合理的起始画面，并过渡至给定的末帧。

请按照 `references/base-en.txt` 中所示的顺序使用 `integrated_multimodal_description`、`overall_soundscape` 和 `non_diegetic_music`。

## 全参考模式 (Full-Reference Mode)

Ref2VA 的重写内容依次包含 `subject_definitions`（主题定义）、`summary`（摘要）、`retention_analysis`（保留分析）、`detailed_description`（详细描述）、`overall_soundscape`（整体声景）和 `non_diegetic_music`（非叙事性音乐）。各部分的参考标签保持一致。

请参阅 `references/ref-en.txt`，了解标签规则、保留分析及完整示例。

## 输出规则

- 重写部分请使用英文撰写；对话、歌词及画面中可见的文字内容须保留原语言（`StyleBrief.user_verbatim` 必须逐字保留）。
- 描述每个镜头时的要素包括：构图、主体、环境、动作、运镜、声音，以及所引用内容出现的精确时间点。
- 避免包含剧情梗概、未解析的引用标签，以及与要求时长不符的时间安排。
