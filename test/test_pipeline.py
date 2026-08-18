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


def test_enhance_t2va_mocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """enhance 编排在 t2va 下应只走 expand + format 两步。"""
    import src.pipeline as pipeline_mod

    def load_prompt_mock(stem: str) -> str:
        return f"SYS_{stem}\n"

    def chat_mock(system: str, user, *, stage: str = "expand") -> str:  # noqa: ANN001
        if stage == "expand":
            return "EXPANDED 16:9 aspect ratio"
        if stage == "format":
            return "RAW_PROMPT 16:9 resolution 768P fps 24."
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(pipeline_mod, "load_prompt", load_prompt_mock)
    monkeypatch.setattr(pipeline_mod, "chat", chat_mock)

    out_dir = tmp_path / "out"
    rec = enhance(
        "t2va",
        "短意图",
        duration=7,
        out_dir=out_dir,
    )

    assert rec["mode"] == "t2va"
    assert rec["duration"] == 7
    assert [s["stage"] for s in rec["steps"]] == ["expand", "format"]

    prompt_text = (out_dir / "prompt.txt").read_text(encoding="utf-8")
    assert "16:9" not in prompt_text
    assert "768P" not in prompt_text


def test_enhance_i2va_requires_first_frame(tmp_path: Path) -> None:
    """i2va 模式缺少 first_frame 应直接报错。"""
    with pytest.raises(ValueError):
        enhance("i2va", "短意图", duration=6, out_dir=tmp_path / "unused")


def test_enhance_i2va_mocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """enhance 在 i2va 下应走 perceive + expand + format。"""
    import src.pipeline as pipeline_mod

    def load_prompt_mock(stem: str) -> str:
        return f"SYS_{stem}\n"

    def chat_mock(system: str, user, *, stage: str = "expand") -> str:  # noqa: ANN001
        if stage == "perceive":
            return "INVENTORY"
        if stage == "expand":
            return "EXPANDED"
        if stage == "format":
            return "RAW_PROMPT 16:9"
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
    )

    assert rec["mode"] == "i2va"
    assert [s["stage"] for s in rec["steps"]] == ["perceive_image", "expand", "format"]
    assert "16:9" not in (out_dir / "prompt.txt").read_text(encoding="utf-8")

