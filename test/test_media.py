from pathlib import Path

import pytest

from src.media import user_parts


def _write_dummy(path: Path, data: bytes) -> None:
    """写入最小字节内容，用于生成 data URI。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_user_parts_types_and_data_uri(tmp_path: Path) -> None:
    """校验 user_parts：视频/音频类型不会再被错误当作图片。"""
    img = tmp_path / "a.png"
    vid = tmp_path / "b.mp4"
    aud = tmp_path / "c.mp3"
    _write_dummy(img, b"img")
    _write_dummy(vid, b"vid")
    _write_dummy(aud, b"aud")

    parts = user_parts(
        "hello",
        images=[str(img)],
        videos=[str(vid)],
        audios=[str(aud)],
    )

    assert parts[0]["type"] == "text"
    types = [p["type"] for p in parts]
    assert "image_url" in types
    assert "video_url" in types
    assert "audio_url" in types

    video_part = next(p for p in parts if p["type"] == "video_url")
    assert video_part["video_url"]["url"].startswith("data:video/")

    audio_part = next(p for p in parts if p["type"] == "audio_url")
    assert audio_part["audio_url"]["url"].startswith("data:audio/")


@pytest.mark.parametrize("kind,type_name", [("image", "image_url"), ("video", "video_url"), ("audio", "audio_url")])
def test_user_parts_presence_single_item(tmp_path: Path, kind: str, type_name: str) -> None:
    """当只传入单个媒体时，对应段的 type 正确。"""
    ext_map = {"image": ".png", "video": ".mp4", "audio": ".mp3"}
    path = tmp_path / f"x{ext_map[kind]}"
    _write_dummy(path, b"x")

    kwargs = {}
    if kind == "image":
        kwargs["images"] = [str(path)]
    elif kind == "video":
        kwargs["videos"] = [str(path)]
    else:
        kwargs["audios"] = [str(path)]

    parts = user_parts("t", **kwargs)
    types = [p["type"] for p in parts]
    assert type_name in types

