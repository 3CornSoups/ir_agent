"""本地图/视频转 Gemini data URI。"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

IMAGE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".heic": "image/heic",
    ".heif": "image/heif",
}
VIDEO_MIME = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".m4v": "video/mp4",
}
AUDIO_MIME = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
}


def _mime(path: Path, table: dict[str, str], fallback: str) -> str:
    """按扩展名推断 MIME。"""
    ext = path.suffix.lower()
    if ext in table:
        return table[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or fallback


def as_data_uri(path: str | Path, kind: str) -> str:
    """把本地文件读成 data URI，供 Gemini 多模态消息使用。"""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"媒体不存在: {p}")
    if kind == "image":
        mime = _mime(p, IMAGE_MIME, "image/jpeg")
    elif kind == "video":
        mime = _mime(p, VIDEO_MIME, "video/mp4")
    elif kind == "audio":
        mime = _mime(p, AUDIO_MIME, "audio/mpeg")
    else:
        raise ValueError(f"未知 kind: {kind}")
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def user_parts(
    text: str,
    *,
    images: list[str] | None = None,
    videos: list[str] | None = None,
    audios: list[str] | None = None,
) -> list[dict]:
    """拼 Gemini USER content：文本 + 图/视频/音频 data URI。"""
    parts: list[dict] = [{"type": "text", "text": text}]
    for p in images or []:
        parts.append({"type": "image_url", "image_url": {"url": as_data_uri(p, "image"), "detail": "high"}})
    for p in videos or []:
        # Gemini 多模态消息里视频需要用 `video_url`，否则上游会按“图片”解析。
        parts.append({"type": "video_url", "video_url": {"url": as_data_uri(p, "video"), "detail": "high"}})
    for p in audios or []:
        # Gemini 多模态消息里音频需要用 `audio_url`，否则上游会按“图片”解析。
        parts.append({"type": "audio_url", "audio_url": {"url": as_data_uri(p, "audio"), "detail": "high"}})
    return parts
