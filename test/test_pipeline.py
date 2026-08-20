from pathlib import Path

import pytest

from src.pipeline import enhance, infer_duration, strip_canvas
from src.video import build_content, resolve_ratio


def test_infer_duration_clamps() -> None:
    """推断时长应限制在 4~15 秒。"""
    assert infer_duration("约 2 秒", fallback=5) == 4
    assert infer_duration("大概 20 秒", fallback=5) == 15
    assert infer_duration("没有时间信息", fallback=5) == 5


def test_strip_canvas_removes_known_tokens() -> None:
    """strip_canvas 应清理画幅/分辨率等误写字段。"""
    raw = "Scene text 16:9 resolution 768P fps 24."
    cleaned = strip_canvas(raw)
    assert "16:9" not in cleaned
    assert "768P" not in cleaned
    assert cleaned.endswith("\n")


@pytest.mark.parametrize(
    "mode,ratio,expected",
    [
        ("t2va", None, "16:9"),
        ("t2va", "21:9", "21:9"),
        ("i2va", None, "adaptive"),
        ("fl2va", None, "adaptive"),
        ("l2va", "9:16", "9:16"),
        ("r2va", "adaptive", "adaptive"),
    ],
)
def test_resolve_ratio(mode: str, ratio: str | None, expected: str) -> None:
    """比例解析规则应符合约束。"""
    assert resolve_ratio(mode, ratio) == expected


def test_resolve_ratio_invalid() -> None:
    """非法比例应抛异常。"""
    with pytest.raises(ValueError):
        resolve_ratio("t2va", "3:3")
    with pytest.raises(ValueError):
        resolve_ratio("i2va", "5:5")


def test_build_content_roles_and_types() -> None:
    """build_content 对不同 mode 的 content 结构应稳定。"""
    prompt = "PROMPT"
    first_frame = "https://example.com/first.png"
    ref_img = "https://example.com/ref.png"
    ref_video = "https://example.com/ref.mp4"
    ref_audio = "https://example.com/ref.mp3"

    c1 = build_content("t2va", prompt)
    assert c1 == [{"type": "text", "text": prompt}]

    c2 = build_content("i2va", prompt, first_frame=first_frame)
    assert c2[0]["type"] == "text"
    assert any(p["type"] == "image_url" and p.get("role") == "first_frame" for p in c2)

    c3 = build_content(
        "r2va",
        prompt,
        reference_images=[ref_img],
        reference_videos=[ref_video],
        reference_audios=[ref_audio],
    )
    roles = [p.get("role") for p in c3 if p["type"] != "text"]
    assert "reference_image" in roles
    assert "reference_video" in roles
    assert "reference_audio" in roles

    last_frame = "https://example.com/last.png"
    c4 = build_content("l2va", prompt, last_frame=last_frame)
    assert any(p.get("role") == "last_frame" for p in c4)

    c5 = build_content("fl2va", prompt, first_frame=first_frame, last_frame=last_frame)
    fl_roles = [p.get("role") for p in c5 if p["type"] != "text"]
    assert fl_roles == ["first_frame", "last_frame"]


def test_enhance_t2va_mocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """enhance 编排在 t2va 下应走 expand + elaborate + format 三步。"""
    import src.pipeline as pipeline_mod

    def load_prompt_mock(stem: str) -> str:
        return f"SYS_{stem}\n"

    def chat_mock(system: str, user, *, stage: str = "expand") -> str:  # noqa: ANN001
        if stage == "route":
            return '{"skills":[]}'
        if stage == "expand":
            return "EXPANDED 16:9 aspect ratio"
        if stage == "elaborate":
            return "EXPANDED_ELAB"
        if stage == "format":
            return (
                "integrated_multimodal_description: [Shot 1] cat 16:9\n\n"
                "overall_soundscape: room tone\n\n"
                "non_diegetic_music: Soft piano at a slow tempo. 768P fps 24."
            )
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(pipeline_mod, "load_prompt", load_prompt_mock)
    monkeypatch.setattr(pipeline_mod, "chat", chat_mock)

    out_dir = tmp_path / "out"
    rec = enhance(
        "t2va",
        "短意图",
        duration=7,
        out_dir=out_dir,
        mechanism_router="off",
    )

    assert rec["mode"] == "t2va"
    assert rec["duration"] == 7
    assert [s["stage"] for s in rec["steps"]] == ["expand", "elaborate", "format"]

    prompt_text = (out_dir / "prompt.txt").read_text(encoding="utf-8")
    assert "16:9" not in prompt_text
    assert "768P" not in prompt_text


def test_enhance_i2va_requires_first_frame(tmp_path: Path) -> None:
    """i2va 模式缺少 first_frame 应直接报错。"""
    with pytest.raises(ValueError):
        enhance("i2va", "短意图", duration=6, out_dir=tmp_path / "unused", mechanism_router="off")


def test_enhance_i2va_mocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """enhance 在 i2va 下应走 perceive + expand + elaborate + format。"""
    import src.pipeline as pipeline_mod

    def load_prompt_mock(stem: str) -> str:
        return f"SYS_{stem}\n"

    def chat_mock(system: str, user, *, stage: str = "expand") -> str:  # noqa: ANN001
        if stage == "route":
            return '{"skills":[]}'
        if stage == "perceive":
            return "INVENTORY"
        if stage == "expand":
            return "EXPANDED"
        if stage == "elaborate":
            return "EXPANDED_ELAB"
        if stage == "format":
            return (
                "For the target video, at 0.00 seconds into the target video, "
                "<Picture 1> (from [Shot 1]) is fully referenced.\n\n"
                "integrated_multimodal_description: [Shot 1] walk forward 16:9\n\n"
                "overall_soundscape: street\n\n"
                "non_diegetic_music: Soft piano at a slow tempo."
            )
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(pipeline_mod, "load_prompt", load_prompt_mock)
    monkeypatch.setattr(pipeline_mod, "chat", chat_mock)

    first_frame = tmp_path / "ff.png"
    first_frame.write_bytes(b"xx")  # 只要存在即可生成 data URI

    out_dir = tmp_path / "out_i2va"
    rec = enhance(
        "i2va",
        "短意图",
        first_frame=str(first_frame),
        duration=6,
        out_dir=out_dir,
        mechanism_router="off",
    )

    assert rec["mode"] == "i2va"
    assert [s["stage"] for s in rec["steps"]] == ["perceive_image", "expand", "elaborate", "format"]
    prompt_text = (out_dir / "prompt.txt").read_text(encoding="utf-8")
    assert "16:9" not in prompt_text
    assert prompt_text.startswith(
        "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced."
    )


def test_enhance_fl2va_requires_both_frames(tmp_path: Path) -> None:
    """fl2va 缺少首帧或尾帧应直接报错。"""
    with pytest.raises(ValueError):
        enhance("fl2va", "短意图", first_frame="a.png", duration=6, out_dir=tmp_path / "unused", mechanism_router="off")
    with pytest.raises(ValueError):
        enhance("fl2va", "短意图", last_frame="b.png", duration=6, out_dir=tmp_path / "unused", mechanism_router="off")


def test_enhance_l2va_requires_last_frame(tmp_path: Path) -> None:
    """l2va 模式缺少 last_frame 应直接报错。"""
    with pytest.raises(ValueError):
        enhance("l2va", "短意图", duration=6, out_dir=tmp_path / "unused", mechanism_router="off")


def test_enhance_fl2va_mocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """enhance 在 fl2va 下应走 perceive + expand + elaborate + format，并对齐句使用真实时长。"""
    import src.pipeline as pipeline_mod

    def load_prompt_mock(stem: str) -> str:
        return f"SYS_{stem}\n"

    def chat_mock(system: str, user, *, stage: str = "expand") -> str:  # noqa: ANN001
        if stage == "route":
            return '{"skills":[]}'
        if stage == "perceive":
            return "INVENTORY"
        if stage == "expand":
            return "EXPANDED"
        if stage == "elaborate":
            return "EXPANDED_ELAB"
        if stage == "format":
            return (
                "How the reference pictures align with the target video — "
                "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
                "Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.\n\n"
                "integrated_multimodal_description: [Shot 1] path 16:9\n\n"
                "overall_soundscape: rain\n\n"
                "non_diegetic_music: Soft piano at a slow tempo."
            )
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(pipeline_mod, "load_prompt", load_prompt_mock)
    monkeypatch.setattr(pipeline_mod, "chat", chat_mock)

    first_frame = tmp_path / "first.png"
    last_frame = tmp_path / "last.png"
    first_frame.write_bytes(b"xx")
    last_frame.write_bytes(b"yy")

    out_dir = tmp_path / "out_fl2va"
    rec = enhance(
        "fl2va",
        "短意图",
        first_frame=str(first_frame),
        last_frame=str(last_frame),
        duration=8,
        out_dir=out_dir,
        mechanism_router="off",
    )

    assert rec["mode"] == "fl2va"
    assert [s["stage"] for s in rec["steps"]] == ["perceive_image", "expand", "elaborate", "format"]
    prompt_text = (out_dir / "prompt.txt").read_text(encoding="utf-8")
    assert "16:9" not in prompt_text
    assert "8.00-second" in prompt_text.split("\n", 1)[0]
    assert "5.00-second" not in prompt_text.split("\n", 1)[0]


def test_enhance_l2va_mocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """enhance 在 l2va 下应按尾帧感知，并对齐句落到最终镜头。"""
    import src.pipeline as pipeline_mod

    def load_prompt_mock(stem: str) -> str:
        return f"SYS_{stem}\n"

    def chat_mock(system: str, user, *, stage: str = "expand") -> str:  # noqa: ANN001
        if stage == "route":
            return '{"skills":[]}'
        if stage == "perceive":
            return "INVENTORY LAST"
        if stage == "expand":
            return "EXPANDED"
        if stage == "elaborate":
            return "EXPANDED_ELAB"
        if stage == "format":
            return (
                "integrated_multimodal_description: [Shot 1] open [Shot 2] At 00:03.000 land\n\n"
                "overall_soundscape: room\n\n"
                "non_diegetic_music: Soft piano at a slow tempo."
            )
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(pipeline_mod, "load_prompt", load_prompt_mock)
    monkeypatch.setattr(pipeline_mod, "chat", chat_mock)

    last_frame = tmp_path / "last.png"
    last_frame.write_bytes(b"zz")
    out_dir = tmp_path / "out_l2va"
    rec = enhance(
        "l2va",
        "从开场切镜，最后落到尾帧",
        last_frame=str(last_frame),
        duration=6,
        out_dir=out_dir,
        mechanism_router="off",
    )
    assert rec["mode"] == "l2va"
    assert [s["stage"] for s in rec["steps"]] == ["perceive_image", "expand", "elaborate", "format"]
    first = rec["prompt"].split("\n", 1)[0]
    assert "<Picture 1> (from [Shot 2])" in first
    assert "6.00-second" in first


def test_compose_format_system_injects_official_guide() -> None:
    """format SYSTEM 必须拼入官方 base/ref 指南，而不是只靠精简 overlay。"""
    from src.skill import compose_format_system, load_official_guide

    overlay = "agent overlay\n"
    t2va_sys = compose_format_system("t2va", overlay)
    r2va_sys = compose_format_system("r2va", overlay)
    assert "agent overlay" in t2va_sys
    assert "integrated_multimodal_description" in t2va_sys
    assert "FL2VA" in t2va_sys
    assert load_official_guide("t2va") in t2va_sys
    assert "subject_definitions" in r2va_sys
    assert load_official_guide("r2va") in r2va_sys

    extra = compose_format_system(
        "t2va",
        overlay,
        extra_guides=[("brand-promo", "Use a product-proof story spine.")],
    )
    assert "style skill: brand-promo" in extra
    assert "official field names/alignment still win" in extra


def test_alignment_line_matches_official_wording() -> None:
    """关键帧对齐句必须与官方 base 指南逐字一致。"""
    from src.skill import alignment_line

    assert alignment_line("i2va", 9) == (
        "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced."
    )
    fl = alignment_line("fl2va", 8, last_shot=1)
    assert "Picture 1 (from Shot 1) aligns with the 0.00-second" in fl
    assert "Picture 2 (from Shot 1) aligns with the 8.00-second" in fl
    lv = alignment_line("l2va", 6, last_shot=3)
    assert lv.startswith("How the reference pictures align with the target video — <Picture 1>")
    assert "(from [Shot 3]) aligns with the 6.00-second" in lv


def test_grid_coverage_gap_requires_all_cells() -> None:
    """声明 3x3 却只写一格时，应判定未看全。"""
    from src.skill import grid_coverage_gap, grid_keep_subjects_note, parse_grid_layout

    incomplete = (
        "Layout: 3x3 grid\n"
        "cell 1,1 — a woman in a blue coat\n"
        "Subjects in this picture: the woman"
    )
    assert parse_grid_layout(incomplete) == (3, 3)
    gap = grid_coverage_gap(incomplete)
    assert gap is not None
    assert "9" in gap
    cells = "\n".join(f"cell {r},{c} — panel" for r in range(1, 4) for c in range(1, 4))
    complete = f"Layout: 3x3 grid\n{cells}\nSubjects in this picture: nine poses of one woman"
    assert grid_coverage_gap(complete) is None
    assert grid_keep_subjects_note(complete)
    assert grid_coverage_gap("Layout: single scene\nA baker at a counter.") is None


def test_perceive_prompts_cover_grid_subjects() -> None:
    """感知/格式化提示词必须要求按格列出 Picture 内全部 Subject。"""
    from src.config import load_prompt

    refs = load_prompt("perceive_refs")
    assert "Subjects in this picture" in refs
    assert "3x3" in refs
    fmt = load_prompt("format_h3")
    assert "one asset may provide multiple subjects" in fmt
    assert "do not create a standalone Picture" in fmt
    assert "One attached file is one <Picture N>" not in fmt
    expand = load_prompt("expand_intent")
    assert "4-grid" in expand or "9-grid" in expand


def test_enhance_r2va_rescans_incomplete_grid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """r2va 宫格首扫只看到一格时，应再扫一次并带上全部格子的 Subject。"""
    import src.pipeline as pipeline_mod

    calls = {"perceive": 0}

    def load_prompt_mock(stem: str) -> str:
        return f"SYS_{stem}\n"

    def chat_mock(system: str, user, *, stage: str = "expand") -> str:  # noqa: ANN001
        if stage == "route":
            return '{"skills":[]}'
        if stage == "perceive":
            calls["perceive"] += 1
            if calls["perceive"] == 1:
                return (
                    "Layout: 2x2 grid\n"
                    "cell 1,1 — only the top-left orange cat\n"
                    "Subjects in this picture: the cat"
                )
            return (
                "Layout: 2x2 grid\n"
                "cell 1,1 — orange cat sitting\n"
                "cell 1,2 — same cat standing\n"
                "cell 2,1 — same cat walking\n"
                "cell 2,2 — same cat sleeping\n"
                "Subjects in this picture: orange tabby cat in four poses"
            )
        if stage == "expand":
            return "EXPANDED four poses of the cat"
        if stage == "elaborate":
            return "EXPANDED_ELAB four poses"
        if stage == "format":
            return (
                "subject_definitions:\n"
                "<Subject 1> is the orange tabby in <Picture 1> cells 1,1–2,2.\n\n"
                "summary:\n[reference generation] cat sheet\n\n"
                "retention_analysis:\n<Subject 1> (appears in [Shot 1]): fully_preserved - coat kept.\n\n"
                "detailed_description:\n[Shot 1] The cat from the sheet walks.\n\n"
                "overall_soundscape: room tone\n\n"
                "non_diegetic_music: Soft piano at a slow tempo."
            )
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(pipeline_mod, "load_prompt", load_prompt_mock)
    monkeypatch.setattr(pipeline_mod, "chat", chat_mock)

    pic = tmp_path / "sheet.png"
    pic.write_bytes(b"xx")
    rec = enhance(
        "r2va",
        "按四宫格人设做动作",
        reference_images=[str(pic)],
        duration=5,
        out_dir=tmp_path / "out_grid",
        mechanism_router="off",
    )
    assert calls["perceive"] == 2
    assert [s["stage"] for s in rec["steps"]] == [
        "perceive_refs",
        "perceive_grid_rescan",
        "expand",
        "elaborate",
        "format",
    ]
    assert "cell 2,2" in rec["inventory"]
    assert "orange tabby cat in four poses" in rec["inventory"]


def test_enhance_loads_brand_skill_from_intent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """品牌宣传意图应在扩写/格式化前注入 brand-promo overlay，且不再问路由模型。"""
    import src.pipeline as pipeline_mod

    seen: dict[str, str] = {}

    def load_prompt_mock(stem: str) -> str:
        return f"SYS_{stem}\n"

    def chat_mock(system: str, user, *, stage: str = "expand") -> str:  # noqa: ANN001
        if stage == "route":
            raise AssertionError("关键词命中后不应再走前置路由模型")
        if stage == "expand":
            seen["expand_user"] = user if isinstance(user, str) else str(user)
            return "EXPANDED brand reel"
        if stage == "elaborate":
            return "EXPANDED_ELAB brand"
        if stage == "format":
            seen["format_sys"] = system
            return (
                "integrated_multimodal_description: [Shot 1] product proof\n\n"
                "overall_soundscape: light UI ticks\n\n"
                "non_diegetic_music: Soft piano at a slow tempo."
            )
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(pipeline_mod, "load_prompt", load_prompt_mock)
    monkeypatch.setattr(pipeline_mod, "chat", chat_mock)

    rec = enhance(
        "t2va",
        "给产品拍一支品牌宣传片，结尾 CTA 和 logo lockup",
        duration=8,
        out_dir=tmp_path / "out_brand",
        skill_router="hybrid",
        mechanism_router="off",
    )
    assert rec["style_skills"] == ["brand-promo"]
    assert rec["style_skill_source"] == "keyword"
    assert [s["stage"] for s in rec["steps"]] == ["skill_route", "expand", "elaborate", "format"]
    assert "style skill: brand-promo" in seen["expand_user"]
    assert "exactly one continuous [Shot 1]" in seen["expand_user"]
    assert "style skill: brand-promo" in seen["format_sys"]
    assert "Official H3 writing guide" in seen["format_sys"]


def test_enhance_forced_skill_off_router(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """router=off 时只加载 --skill 指定的 overlay。"""
    import src.pipeline as pipeline_mod

    def load_prompt_mock(stem: str) -> str:
        return f"SYS_{stem}\n"

    def chat_mock(system: str, user, *, stage: str = "expand") -> str:  # noqa: ANN001
        if stage == "route":
            raise AssertionError("off 模式不应调用路由模型")
        if stage == "expand":
            assert "style skill: 3d-animation" in (user if isinstance(user, str) else "")
            return "EXPANDED"
        if stage == "elaborate":
            return "EXPANDED_ELAB"
        if stage == "format":
            assert "style skill: 3d-animation" in system
            return (
                "integrated_multimodal_description: [Shot 1] cartoon\n\n"
                "overall_soundscape: N/A\n\n"
                "non_diegetic_music: Soft piano at a slow tempo."
            )
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(pipeline_mod, "load_prompt", load_prompt_mock)
    monkeypatch.setattr(pipeline_mod, "chat", chat_mock)

    rec = enhance(
        "t2va",
        "一只橘猫在窗台晒太阳",
        duration=5,
        out_dir=tmp_path / "out_forced",
        skills=["3d-animation"],
        skill_router="off",
        mechanism_router="off",
    )
    assert rec["style_skills"] == ["3d-animation"]
    assert rec["style_skill_source"] == "forced"

