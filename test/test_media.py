from pathlib import Path

import pytest

from src.gemini import _to_native_parts
from src.media import VIDEO_INLINE_LIMIT_BYTES, as_data_uri, user_parts


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


def test_to_native_parts_text_only() -> None:
    """纯文本 user 应转成单个 text part。"""
    native = _to_native_parts("just text")
    assert native == [{"text": "just text"}]


def test_to_native_parts_converts_media(tmp_path: Path) -> None:
    """原生协议：image_url/video_url/audio_url 应转成 inlineData（含 mime 与 base64）。"""
    img = tmp_path / "a.png"
    vid = tmp_path / "b.mp4"
    aud = tmp_path / "c.mp3"
    _write_dummy(img, b"imgdata")
    _write_dummy(vid, b"viddata")
    _write_dummy(aud, b"auddata")

    parts = user_parts(
        "hello",
        images=[str(img)],
        videos=[str(vid)],
        audios=[str(aud)],
    )
    native = _to_native_parts(parts)

    texts = [p["text"] for p in native if "text" in p]
    assert texts == ["hello"]

    blobs = [p["inlineData"] for p in native if "inlineData" in p]
    mimes = {b["mimeType"] for b in blobs}
    assert mimes == {"image/png", "video/mp4", "audio/mpeg"}
    for b in blobs:
        assert b["data"], "inlineData 不应为空 base64"
    assert len(native) == 4, "应包含 1 文本 + 3 媒体"


def test_to_native_parts_plain_str() -> None:
    """混合列表里未知类型应被跳过，不产生非法 part。"""
    parts = [
        {"type": "text", "text": "a"},
        {"type": "mystery", "mystery": {"url": "x"}},
    ]
    native = _to_native_parts(parts)
    assert native == [{"text": "a"}]


def test_as_data_uri_under_limit_not_compressed(tmp_path: Path) -> None:
    """未超限的视频传入 max_video_bytes 应原样发送，不被压缩。"""
    vid = tmp_path / "small.mp4"
    payload = b"v" * 1024
    _write_dummy(vid, payload)
    uri = as_data_uri(vid, "video", max_video_bytes=VIDEO_INLINE_LIMIT_BYTES)
    assert uri.startswith("data:video/mp4;base64,")
    import base64 as _b64

    decoded = _b64.b64decode(uri.split(",", 1)[1])
    assert decoded == payload, "未超限视频应原样 base64，不做任何压缩"


def test_as_data_uri_over_limit_compresses(tmp_path: Path) -> None:
    """超限视频传 max_video_bytes 应触发 ffmpeg 压缩，产物 mime 固定 mp4 且达标。"""
    import shutil
    from pathlib import Path as _P

    if shutil.which("ffmpeg") is None:
        pytest.skip("需要 ffmpeg")
    # 项目内置真实视频（31MB）作为压缩素材
    src = _P(__file__).resolve().parent.parent / "runs/generated_media/cases/char_action/action.mov"
    if not src.is_file():
        pytest.skip(f"缺少素材: {src}")
    assert src.stat().st_size > VIDEO_INLINE_LIMIT_BYTES, "素材应超过 20MB 阈值"
    uri = as_data_uri(src, "video", max_video_bytes=VIDEO_INLINE_LIMIT_BYTES)
    assert uri.startswith("data:video/mp4;base64,")
    import base64 as _b64

    compressed = _b64.b64decode(uri.split(",", 1)[1])
    assert len(compressed) <= VIDEO_INLINE_LIMIT_BYTES, "压缩产物应落在 20MB 内"
    assert len(compressed) < src.stat().st_size // 2, "压缩应有明显体积下降"


def test_as_data_uri_h3_path_no_compress(tmp_path: Path) -> None:
    """H3 出片路径不传 max_video_bytes，超限视频也应原样保留（不压缩）。"""
    vid = tmp_path / "big_h3.mp4"
    payload = b"h" * (VIDEO_INLINE_LIMIT_BYTES + 1024)  # 21MB 假视频
    _write_dummy(vid, payload)
    uri = as_data_uri(vid, "video")  # 不传 max_video_bytes
    assert uri.startswith("data:video/mp4;base64,")
    import base64 as _b64

    decoded = _b64.b64decode(uri.split(",", 1)[1])
    assert decoded == payload, "H3 路径应原样发送，不做压缩"
