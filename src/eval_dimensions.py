"""十八维评估维度定义：供本地裁判模型打分与报告渲染。"""

from __future__ import annotations

from typing import Any

# id → (中文名, 英文 key, 评分要点)
DIMENSIONS: tuple[dict[str, str], ...] = (
    {
        "id": "d01_instruction_following",
        "name": "指令理解与遵循",
        "rubric": "短意图中的主体、动作、风格、禁止项、时长节奏是否被提示词完整保留，无跑题/漏要点/擅自改结局。",
    },
    {
        "id": "d02_visual_quality",
        "name": "视觉质量",
        "rubric": "外观、材质、光影、构图是否写清；是否避免无法执行的微物理堆砌，同时仍足够具体。",
    },
    {
        "id": "d03_temporal_stability",
        "name": "时序稳定性",
        "rubric": "时间戳/镜头顺序是否单调合理；切镜与节拍是否连贯，无时间线跳跃或自相矛盾。",
    },
    {
        "id": "d04_character_consistency",
        "name": "角色一致性",
        "rubric": "人物/动物身份、衣着覆盖、发型配饰是否前后锁定；有库存时是否等精度保留。",
    },
    {
        "id": "d05_action_quality",
        "name": "动作质量",
        "rubric": "动作起止、幅度、节奏是否可拍；是否用动作级语言而非空洞形容词。",
    },
    {
        "id": "d06_human_object_interaction",
        "name": "人物与物体交互",
        "rubric": "手部接触、道具交接、受力关系是否写清楚；意图含交互时不得省略。",
    },
    {
        "id": "d07_physics_plausibility",
        "name": "物理合理性",
        "rubric": "重力、惯性、布料/流体等是否用模型可执行的动作语言；无离谱违背常识的指令。",
    },
    {
        "id": "d08_spatial_3d",
        "name": "空间与三维一致性",
        "rubric": "前后景层次、左右方位、遮挡关系是否稳定；关键帧模式是否尊重参考空间布局。",
    },
    {
        "id": "d09_camera_control",
        "name": "镜头控制",
        "rubric": "机位运动类型/幅度/速度是否明确且必要；无乱加镜头导致叙事涣散。",
    },
    {
        "id": "d10_shot_scale_language",
        "name": "景别与摄影语言",
        "rubric": "景别、构图重心、焦点是否服务意图；摄影语汇是否准确而非堆砌术语。",
    },
    {
        "id": "d11_multi_subject",
        "name": "多主体能力",
        "rubric": "多人物/多物体同时在场时是否各自可辨、关系清楚；宫格多主体是否全覆盖。",
    },
    {
        "id": "d12_reference_control",
        "name": "参考图/条件控制能力",
        "rubric": "有参考库存时，标签引用、对齐句、保留分析是否正确；无参考则标 N/A。",
    },
    {
        "id": "d13_style_visual_control",
        "name": "风格与视觉控制能力",
        "rubric": "画风/题材写法是否与意图匹配；风格 skill 注入是否过强或跑偏。",
    },
    {
        "id": "d14_edit_controllability",
        "name": "编辑与可控生成能力",
        "rubric": "用户可改要素（台词、CTA、禁止项）是否可定位、可核查；字段骨架是否利于二次编辑。",
    },
    {
        "id": "d15_audio_generation",
        "name": "音频生成能力",
        "rubric": "环境声、动作声、配乐字段是否齐全；有对白时是否原语言锁定在 <d> 内。",
    },
    {
        "id": "d16_av_sync",
        "name": "音视频同步能力",
        "rubric": "声画节拍、说话口型时机、切镜与音效是否在文本层对齐描述。",
    },
    {
        "id": "d17_failure_control",
        "name": "失败与异常控制",
        "rubric": "是否规避常见坏 case：译成英文对白、丢台词、降覆盖、画幅残留、发明未上传素材。",
    },
    {
        "id": "d18_text_subtitle",
        "name": "文字与字幕能力",
        "rubric": "屏上文字/字幕/logo 文案是否保留原语言与内容；意图无文字需求则标 N/A。",
    },
)


def dimension_catalog_text() -> str:
    """生成裁判 SYSTEM 用的维度清单。"""
    lines = ["Score each dimension 1-5 (integer), or null if not applicable (N/A):"]
    for i, dim in enumerate(DIMENSIONS, start=1):
        lines.append(f"{i}. {dim['id']} ({dim['name']}): {dim['rubric']}")
    return "\n".join(lines)


def empty_score_skeleton() -> dict[str, Any]:
    """返回空分数骨架，便于解析失败时落盘。"""
    return {dim["id"]: None for dim in DIMENSIONS}
