# -*- coding: utf-8 -*-
"""Agent vs 官方 Context-IR 管线对比测试用例清单。

用例来源两类：
1. 从 zwb 目录现成资产中找的对照用例（source="zwb-asset"，含官方 IR 输出 official_*）
2. 需调用本地文生图/文生视频模型生成素材的用例（source="local-model"，由 test_local_model_cases 驱动）

官方 IR 对照资产位置：
- 母仓基准：/kwkj-k8s/zwb/应用/Qwen提示词/母仓/benchmarks/
- R2VA 对照项目：/kwkj-k8s/zwb/项目/R2VA_Qwen与官方对照/

字段命名：agent（enhance）与官方 IR 的 t2va 均为三字段，r2va 均为六段，
字段名一致，可直接按字段名做结构对比。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ---- zwb 资产根目录（跨机器可改） -------------------------------------------------
ZW = Path("/kwkj-k8s/zwb")
MUCA = ZW / "应用" / "Qwen提示词" / "母仓" / "benchmarks"
MUCA_ROOT = ZW / "应用" / "Qwen提示词" / "母仓"
R2VA_CMP = ZW / "项目" / "R2VA_Qwen与官方对照"
FL2VA_CASES = ZW / "项目" / "FL2VA用例"

# 官方 IR 输出 / 本地 Qwen 输出的对照目录
GOLD_IR = MUCA_ROOT / "out" / "gold_ir"            # 四模式（t2va/i2va/fl2va/l2va）官方增强稿
GOLD_IR_R2VA = MUCA_ROOT / "out" / "gold_ir_r2va"  # r2va 官方增强稿
QWEN_V1 = MUCA_ROOT / "out" / "qwen_intent_v1.1"   # 四模式本地 Qwen 增强稿
QWEN_R2VA = MUCA_ROOT / "out" / "qwen_intent_r2va_v1"  # r2va 本地 Qwen 增强稿
GOLD_IR_VIDEO = MUCA_ROOT / "out" / "videos" / "gold_ir"
QWEN_VIDEO = MUCA_ROOT / "out" / "videos" / "qwen_intent_v1.1"

# 官方 IR 输出的字段骨架（与 format_h3 一致）
T2VA_FIELDS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")
R2VA_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
# i2va/fl2va/l2va = 帧对齐句 + 三字段
FRAME_ALIGN_FIELDS = T2VA_FIELDS

# 帧对齐句的特征词（官方 IR 各模式的帧对齐句式）
ALIGN_HINTS = {
    "i2va": "fully referenced",
    "fl2va": "aligns with the 0.00-second",
    "l2va": "aligns with the",
}

# 不允许写进 prompt 的画幅/分辨率/帧率标记（agent 的 strip_canvas 会清理）
FORBIDDEN_TOKENS = ("16:9", "9:16", "21:9", "4:3", "768P", "2K", "1080P", "fps", "1280x720", "1920x1080")

# 用例素材集中目录：runs/generated_media/cases/<case_id>/（复制自 zwb 资产）
MEDIA_CASES_DIR = Path("/kwkj-k8s/zwb/项目/agent/new_agent0818/runs/generated_media/cases")


def _p(*parts: str) -> str:
    """把 zwb 相对路径拼成绝对路径字符串。"""
    return str(Path(*parts))


def read_intent(path: str) -> str:
    """读取意图文本（UTF-8）。"""
    return Path(path).read_text(encoding="utf-8").strip()


def _gold_ir(mode_id: str) -> str:
    """母仓 gold_ir 官方增强稿路径。"""
    return str(GOLD_IR / f"{mode_id}.txt")


def _gold_ir_r2va(case_id: str) -> str:
    """母仓 gold_ir_r2va 官方增强稿路径。"""
    return str(GOLD_IR_R2VA / f"{case_id}.txt")


def _qwen_v1(mode_id: str) -> str:
    """母仓 qwen_intent_v1.1 本地 Qwen 增强稿路径。"""
    return str(QWEN_V1 / f"{mode_id}.txt")


def _qwen_r2va(case_id: str) -> str:
    """母仓 qwen_intent_r2va_v1 本地 Qwen 增强稿路径。"""
    return str(QWEN_R2VA / f"{case_id}.txt")


def _gold_video(mode_id: str) -> str:
    """母仓 gold_ir 官方成片路径。"""
    return str(GOLD_IR_VIDEO / f"{mode_id}.mp4")


def _qwen_video(mode_id: str) -> str:
    """母仓 qwen_intent_v1.1 本地成片路径。"""
    return str(QWEN_VIDEO / f"{mode_id}.mp4")


# ---- 母仓四模式基准：t2va/i2va/fl2va/l2va 各有官方稿 + Qwen 稿 + 成片 ----
_FOUR_MODE_SETS: dict[str, dict[str, Any]] = {
    # id -> dict(mode, intent, duration, first_frame, last_frame)
    "wuxia_t2va": {"mode": "t2va", "set": "set_01_wuxia", "intent": "t2va_intent.txt", "duration": 9, "ratio": "16:9"},
    "wuxia_i2va": {"mode": "i2va", "set": "set_01_wuxia", "intent": "i2va_intent.txt", "duration": 9, "ratio": "16:9",
                   "first_frame": "first.png"},
    "wuxia_fl2va": {"mode": "fl2va", "set": "set_01_wuxia", "intent": "fl2va_intent.txt", "duration": 9, "ratio": "16:9",
                    "first_frame": "first.png", "last_frame": "last.png"},
    "wuxia_l2va": {"mode": "l2va", "set": "set_01_wuxia", "intent": "l2va_intent.txt", "duration": 9, "ratio": "16:9",
                   "last_frame": "last.png"},
    "neon_t2va": {"mode": "t2va", "set": "set_02_neon_t2va", "intent": "t2va_intent.txt", "duration": 6, "ratio": "16:9"},
    "cotton_fl2va": {"mode": "fl2va", "set": "set_03_cotton", "intent": "fl2va_intent.txt", "duration": 8, "ratio": "16:9",
                     "first_frame": "first.png", "last_frame": "last.png"},
    "office_fl2va": {"mode": "fl2va", "set": "set_04_office", "intent": "fl2va_intent.txt", "duration": 6, "ratio": "16:9",
                     "first_frame": "first.png", "last_frame": "last.png"},
}

# 母仓 r2va 基准：官方稿在 gold_ir_r2va（文件名不带 r2va_ 前缀）
_R2VA_SETS: dict[str, dict[str, Any]] = {
    "char_action": {"set": "set_r2va_char_action", "intent": "intent.txt", "duration": 5,
                    "images": ["char.png"], "videos": ["action.mov"]},
    "continuation": {"set": "set_r2va_continuation", "intent": "intent.txt", "duration": 5,
                     "images": ["end_frame.png"], "videos": ["source.mp4"]},
    "cont_keyframe": {"set": "set_r2va_cont_keyframe", "intent": "intent.txt", "duration": 5,
                      "images": ["end_frame.png"]},
    "style_transfer": {"set": "set_r2va_style_transfer", "intent": "intent.txt", "duration": 5,
                       "images": ["style.png"], "videos": ["motion.mp4"]},
    "style_new_plot": {"set": "set_r2va_style_new_plot", "intent": "intent.txt", "duration": 5,
                       "images": ["style.png"], "videos": ["style_video.mp4"]},
    "remove_overlay": {"set": "set_r2va_remove_overlay", "intent": "intent.txt", "duration": 5,
                       "videos": ["source.mp4"]},
}

# FL2VA 自建用例（官方增强稿 enhanced_en.txt + 成片）
_FL2VA_BUILTIN = {
    "fl2va_cotton_domain": {
        "root": "用例01_棉花领域模型", "intent": "03_短意图/fl2va_input_zh.txt",
        "first_frame": "02_处理后首尾帧/first.png", "last_frame": "02_处理后首尾帧/last.png",
        "official_prompt": "04_ContextIR增强/enhanced_en.txt",
        "official_video": "05_生成视频/fl2va_cotton.mp4", "duration": 8, "ratio": "16:9",
    },
    "fl2va_office_quarrel": {
        "root": "用例02_董事总经理吵架", "intent": "03_短意图/fl2va_input_zh.txt",
        "first_frame": "02_处理后首尾帧/first.png", "last_frame": "02_处理后首尾帧/last.png",
        "official_prompt": "04_ContextIR增强/enhanced_en.txt",
        "official_video": "05_生成视频/fl2va_quarrel.mp4", "duration": 6, "ratio": "16:9",
    },
}

# special_light 特殊光影 T2VA 双管线用例（官方 + 本地 Qwen 各一份）
_LIGHT_CASES = {
    "light_backlight": "backlight_逆光",
    "light_neon": "neon_霓虹",
    "light_dusk": "dusk_黄昏",
}


def _relocate_media(case: dict[str, Any]) -> dict[str, Any]:
    """把用例媒体路径重定位到 generated_media/cases/<case_id>/，缺失时回退原 zwb 路径。"""
    cid = case["id"]
    case_dir = MEDIA_CASES_DIR / cid

    def one(p: str) -> str:
        cand = case_dir / Path(p).name
        return str(cand) if cand.is_file() else p

    for key in ("first_frame", "last_frame", "reference_images", "reference_videos", "reference_audios"):
        value = case.get(key)
        if value is None:
            continue
        case[key] = [one(p) for p in value] if isinstance(value, list) else one(value)
    return case


def build_cases() -> list[dict[str, Any]]:
    """构造完整用例清单。

    分组：
    1. 母仓四模式基准（t2va/i2va/fl2va/l2va）：官方稿 gold_ir/ + Qwen 稿 qwen_intent_v1.1/ + 成片
    2. R2VA 对照项目（风伯/采莲/时装多图）：官方六段稿 + 素材
    3. 母仓 r2va 基准（动作/续写/关键帧/风格迁移/新情节/去水印）：官方稿 gold_ir_r2va/
    4. FL2VA 自建用例（棉花/吵架）：官方增强稿 + 成片
    5. special_light 特殊光影 T2VA（逆光/霓虹/黄昏）：官方 + Qwen 双稿
    """
    cases: list[dict[str, Any]] = []

    # ---- 1. 母仓四模式基准 ----------------------------------------------------
    for mode_id, meta in _FOUR_MODE_SETS.items():
        set_dir = MUCA / meta["set"]
        agent_supported = meta["mode"] in ("t2va", "i2va", "fl2va", "l2va", "r2va")
        case: dict[str, Any] = {
            "id": mode_id,
            "mode": meta["mode"],
            "source": "zwb-asset",
            "agent_supported": agent_supported,
            "intent": read_intent(str(set_dir / meta["intent"])),
            "first_frame": _p(set_dir, meta["first_frame"]) if meta.get("first_frame") else None,
            "last_frame": _p(set_dir, meta["last_frame"]) if meta.get("last_frame") else None,
            "duration": meta["duration"],
            "ratio": meta["ratio"],
            "official_prompt": _gold_ir(mode_id),
            "local_qwen_prompt": _qwen_v1(mode_id),
            "official_video": _gold_video(mode_id),
            "local_qwen_video": _qwen_video(mode_id),
            "note": f"母仓 {meta['set']}：官方稿/本地 Qwen 稿/双管线成片齐全",
        }
        # R2VA 对照项目另有 agent 实测稿与官方稿副本（同一批短意图）
        if mode_id == "wuxia_t2va":
            case["cmp_official_prompt"] = str(R2VA_CMP / "04_wuxia_t2va" / "提示词" / "official_t2va.txt")
            case["agent_run_prompt"] = str(R2VA_CMP / "04_wuxia_t2va" / "提示词" / "agent_qwen38_v12.txt")
        elif mode_id == "neon_t2va":
            case["cmp_official_prompt"] = str(R2VA_CMP / "05_neon_t2va" / "提示词" / "official_t2va.txt")
            case["agent_run_prompt"] = str(R2VA_CMP / "05_neon_t2va" / "提示词" / "agent_qwen38_v12.txt")
        cases.append(case)

    # ---- 2. R2VA 对照项目（风伯/采莲/时装多图） --------------------------------
    fengbo_input = R2VA_CMP / "01_风伯飙马" / "输入"
    cailian_input = R2VA_CMP / "02_采莲水鸟" / "输入"
    shizhuang_input = R2VA_CMP / "03_时装多图" / "输入"
    cases += [
        {
            "id": "fengbo_r2va",
            "mode": "r2va",
            "source": "zwb-asset",
            "agent_supported": True,
            "intent": read_intent(str(fengbo_input / "intent.txt")),
            "reference_images": [_p(fengbo_input, n) for n in
                                 ("风伯架飙马.jpg", "风伯与飙马.jpg", "风伯与飙马2.jpg", "风伯与飙马的原始拓片.png")],
            "duration": 10,
            "ratio": "16:9",
            "official_prompt": str(R2VA_CMP / "01_风伯飙马" / "提示词" / "official_r2va.txt"),
            "note": "汉画像石拓片四图：官方 IR 六段输出已留档",
        },
        {
            "id": "cailian_r2va",
            "mode": "r2va",
            "source": "zwb-asset",
            "agent_supported": True,
            "intent": read_intent(str(cailian_input / "intent.txt")),
            "reference_images": [_p(cailian_input, n) for n in
                                 ("水鸟和荷花.jpg", "水鸟和荷花2.jpg", "大暑_采莲.png", "大暑_采莲红色.png")],
            "duration": 10,
            "ratio": "16:9",
            "official_prompt": str(R2VA_CMP / "02_采莲水鸟" / "提示词" / "official_r2va.txt"),
            "note": "墨彩水鸟四图：官方 IR 六段输出已留档",
        },
        {
            "id": "r2va_multi_img",
            "mode": "r2va",
            "source": "zwb-asset",
            "agent_supported": True,
            "intent": read_intent(str(shizhuang_input / "intent.txt")),
            "reference_images": [_p(shizhuang_input, n) for n in ("ref1.jpg", "ref2.jpg", "ref3.jpg")],
            "duration": 6,
            "ratio": "16:9",
            "official_prompt": _gold_ir_r2va("multi_img"),
            "cmp_official_prompt": str(R2VA_CMP / "03_时装多图" / "提示词" / "official_r2va.txt"),
            "local_qwen_prompt": str(R2VA_CMP / "03_时装多图" / "提示词" / "qwen_r2va.txt"),
            "note": "时装多图：官方六段稿在 gold_ir_r2va/ 与 03_时装多图/提示词 各一份，人物/眼镜/服装一致性",
        },
    ]

    # ---- 3. 母仓 r2va 基准（官方稿 gold_ir_r2va） ------------------------------
    for case_id, meta in _R2VA_SETS.items():
        set_dir = MUCA / meta["set"]
        cases.append({
            "id": case_id,
            "mode": "r2va",
            "source": "zwb-asset",
            "agent_supported": True,
            "intent": read_intent(str(set_dir / meta["intent"])),
            "reference_images": [_p(set_dir, n) for n in meta.get("images", [])] or None,
            "reference_videos": [_p(set_dir, n) for n in meta.get("videos", [])] or None,
            "duration": meta["duration"],
            "ratio": "16:9",
            "official_prompt": _gold_ir_r2va(case_id),
            "local_qwen_prompt": _qwen_r2va(case_id),
            "note": f"母仓 {meta['set']}：官方稿在 gold_ir_r2va/，本地 Qwen 稿在 qwen_intent_r2va_v1/",
        })

    # ---- 4. FL2VA 自建用例（棉花/吵架） ----------------------------------------
    for case_id, meta in _FL2VA_BUILTIN.items():
        root = FL2VA_CASES / meta["root"]
        cases.append({
            "id": case_id,
            "mode": "fl2va",
            "source": "zwb-asset",
            "agent_supported": True,
            "intent": read_intent(str(root / meta["intent"])),
            "first_frame": str(root / meta["first_frame"]),
            "last_frame": str(root / meta["last_frame"]),
            "duration": meta["duration"],
            "ratio": meta["ratio"],
            "official_prompt": str(root / meta["official_prompt"]),
            "official_video": str(root / meta["official_video"]),
            "note": f"自建 FL2VA（{meta['root']}）：短意图→Context-IR→本地成片 全链路留档",
        })

    # 特殊光影 T2VA（backlight/neon/dusk）-----------------------------------
    light_root = MUCA_ROOT / "out" / "special_light"
    for case_id, stem in _LIGHT_CASES.items():
        cases.append({
            "id": case_id,
            "mode": "t2va",
            "source": "zwb-asset",
            "agent_supported": True,
            "intent": read_intent(str(light_root / "intents" / f"{stem}.txt")),
            "duration": 6,
            "ratio": "16:9",
            "official_prompt": str(light_root / "official" / f"{stem}.txt"),
            "local_qwen_prompt": str(light_root / "qwen" / f"{stem}.txt"),
            "note": f"特殊光影 T2VA（{stem}）：官方/本地 Qwen 双稿在 special_light/",
        })

    return [_relocate_media(c) for c in cases]


def case_by_id(case_id: str) -> dict[str, Any]:
    """按 id 取单个用例。"""
    for c in build_cases():
        if c["id"] == case_id:
            return c
    raise KeyError(f"未知用例: {case_id}")


def expected_fields(mode: str) -> tuple[str, ...]:
    """按模式返回期望的官方字段骨架。"""
    mode = mode.lower()
    if mode == "r2va":
        return R2VA_FIELDS
    # t2va / i2va / fl2va / l2va 均为「三字段」；帧对齐模式另有对齐句
    return T2VA_FIELDS


def is_frame_aligned(mode: str) -> bool:
    """是否帧对齐模式（i2va/fl2va/l2va，输出前有对齐句）。"""
    return mode.lower() in ("i2va", "fl2va", "l2va")


def align_hint(mode: str) -> str:
    """帧对齐模式的官方对齐句特征词。"""
    return ALIGN_HINTS[mode.lower()]
