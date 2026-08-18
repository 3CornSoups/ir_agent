# -*- coding: utf-8 -*-
"""本地模型生成素材的用例测试。

素材来自 runs/generated_media/（由 test_local_h3_generation.py 生成，见其 docstring）。
若素材缺失则 skip，并提示先跑本地出片测试：
    RUN_LOCAL_H3_MEDIA_TESTS=1 pytest -q test/test_local_h3_generation.py -k local_h3

这类用例验证：agent 能把“本地文生图/文生视频模型”产出的媒体当作
i2va 首帧 / r2va 参考图 / r2va 参考视频 来消费，并产出正确结构。
对比官方 IR 时，可用同一素材喂官方 Context-IR 脚本（见 test_cases.md）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cases_agent_vs_official import FORBIDDEN_TOKENS, expected_fields

# 本地生成素材目录（对齐 test_local_h3_generation.py 的输出位置）
LOCAL_MEDIA_DIR = Path(__file__).resolve().parents[1] / "runs" / "generated_media"

# 本地素材 → 用例映射：验证这些素材能作为 i2va/r2va 的输入
LOCAL_CASES = [
    {
        "id": "local_t2va_video_as_r2va_ref",
        "mode": "r2va",
        "intent": "参考图中的角色做出参考视频中的动作：自然肢体动力学，电影光影，约 5 秒。保持角色外观与参考图一致，动作节奏与参考视频一致。",
        "reference_images": [str(LOCAL_MEDIA_DIR / "r2va_参考图.png")],
        "reference_videos": [str(LOCAL_MEDIA_DIR / "t2va_测试参考.mp4")],
        "note": "本地 t2va 生成的视频作为 r2va 动作参考",
    },
    {
        "id": "local_i2va_firstframe",
        "mode": "i2va",
        "intent": "从首帧人物落脚开始，镜头缓慢推进到集市，保持 3D 风格与暖色日光。",
        "first_frame": str(LOCAL_MEDIA_DIR / "i2va_参考首帧.png"),
        "note": "本地生成的图作为 i2va 首帧",
    },
    {
        "id": "local_generated_video_as_ref",
        "mode": "r2va",
        "intent": "借鉴参考视频的材质、暖棚灯光与手作质感，按新情节生成约 5 秒短片：粘土狐狸用木勺搅拌陶碗里的果酱，无对白。",
        "reference_videos": [str(LOCAL_MEDIA_DIR / "i2va_测试参考.mp4")],
        "note": "本地 i2va 生成的视频作为 r2va 参考视频",
    },
    {
        "id": "local_r2va_video_as_ref",
        "mode": "r2va",
        "intent": "参考图中的角色做出参考视频中的动作：自然肢体动力学，电影光影，约 5 秒。保持角色外观与参考图一致，动作节奏与参考视频一致。",
        "reference_images": [str(LOCAL_MEDIA_DIR / "r2va_参考图.png")],
        "reference_videos": [str(LOCAL_MEDIA_DIR / "r2va_测试参考.mp4")],
        "note": "本地 r2va 生成的成片（含音频）作为新的 r2va 参考",
    },
]


def _media_paths(case: dict) -> list[str]:
    """把用例里的媒体字段统一成路径列表。"""
    paths: list[str] = []
    for key in ("first_frame", "reference_images", "reference_videos"):
        value = case.get(key)
        if value:
            paths.extend(value if isinstance(value, list) else [value])
    return paths


def _require_local_media(case: dict) -> None:
    """本地素材缺失时 skip，并给出生成指引。"""
    missing = [p for p in _media_paths(case) if not Path(p).is_file()]
    if missing:
        pytest.skip(
            f"本地素材缺失（{missing}）。先运行：\n"
            "RUN_LOCAL_H3_MEDIA_TESTS=1 pytest -q test/test_local_h3_generation.py -k local_h3"
        )


@pytest.mark.parametrize("case", LOCAL_CASES, ids=[c["id"] for c in LOCAL_CASES])
def test_local_model_media_as_input(case: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """本地生成素材应能作为 i2va/r2va 输入跑 enhance，且产物结构正确、无画幅残留。"""
    _require_local_media(case)

    import src.pipeline as pipeline_mod

    def load_prompt_mock(stem: str) -> str:
        return f"SYS_{stem}\n"

    def chat_mock(system: str, user, *, stage: str = "expand") -> str:  # noqa: ANN001
        if stage == "perceive":
            return "INVENTORY: <Picture 1> 角色外观; <Video 1> 动作节奏"
        if stage == "expand":
            return "EXPANDED SCENE"
        fields = expected_fields(case["mode"])
        return "\n\n".join(f"{f}: [Shot 1] placeholder" for f in fields)

    monkeypatch.setattr(pipeline_mod, "load_prompt", load_prompt_mock)
    monkeypatch.setattr(pipeline_mod, "chat", chat_mock)

    from src.pipeline import enhance

    rec = enhance(
        case["mode"],
        case["intent"],
        first_frame=case.get("first_frame"),
        reference_images=case.get("reference_images"),
        reference_videos=case.get("reference_videos"),
        reference_audios=case.get("reference_audios"),
        duration=5,
        out_dir=tmp_path / f"out_{case['id']}",
    )
    prompt = rec["prompt"]
    for f in expected_fields(case["mode"]):
        assert prompt.startswith(f"{f}:") or f"\n{f}:" in prompt, f"{case['id']}: 缺字段 {f}"
    for token in FORBIDDEN_TOKENS:
        assert token not in prompt, f"{case['id']}: prompt 含 {token!r}"


@pytest.mark.parametrize("case", LOCAL_CASES, ids=[c["id"] for c in LOCAL_CASES])
def test_local_model_media_build_content(case: dict) -> None:
    """本地素材应能构造出带正确 role 的 H3 content。"""
    _require_local_media(case)

    from src.video import build_content

    content = build_content(
        case["mode"],
        "PROMPT_PLACEHOLDER",
        first_frame=case.get("first_frame"),
        reference_images=case.get("reference_images"),
        reference_videos=case.get("reference_videos"),
        reference_audios=case.get("reference_audios"),
    )
    roles = [p.get("role") for p in content if p.get("type") != "text"]
    if case["mode"] == "i2va":
        assert "first_frame" in roles, roles
    if case.get("reference_images"):
        assert "reference_image" in roles, roles
    if case.get("reference_videos"):
        assert "reference_video" in roles, roles


def test_local_media_metadata() -> None:
    """本地生成素材应满足 agent 消费的基本属性（存在、非空、MP4/PNG）。"""
    if not LOCAL_MEDIA_DIR.is_dir():
        pytest.skip("runs/generated_media 不存在，先跑本地出片测试")
    # 只检查根目录的媒体文件（cases/ 子目录与 README.md 不算媒体）
    files = [p for p in LOCAL_MEDIA_DIR.iterdir()
             if p.is_file() and p.suffix in {".mp4", ".png", ".mov", ".jpg"}]
    assert files, f"{LOCAL_MEDIA_DIR} 根目录无媒体文件"
    for p in files:
        assert p.stat().st_size > 1024, f"素材异常: {p}"
