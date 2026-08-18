"""本地图/视频转 Gemini data URI。"""

from __future__ import annotations

import base64
import mimetypes
import shutil
import subprocess
import tempfile
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

# Gemini 原生端点 inlineData 上限（Blob data，字节）；超过即触发 ffmpeg 压缩兜底。
VIDEO_INLINE_LIMIT_BYTES = 20 * 1024 * 1024
# 压缩目标：留出 base64 放大（约 4/3 倍）余量，保证压缩产物 + base64 后仍落在上限内。
VIDEO_TARGET_BYTES = 15 * 1024 * 1024


def _mime(path: Path, table: dict[str, str], fallback: str) -> str:
    """按扩展名推断 MIME。"""
    ext = path.suffix.lower()
    if ext in table:
        return table[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or fallback


def _compress_video_bytes(src: Path) -> bytes | None:
    """用 ffmpeg 把视频压到 VIDEO_TARGET_BYTES 以内；失败返回 None。

    策略：先按原分辨率压（crf 28），仍超限则缩到 1280 宽再压一次；
    产物固定转码为 mp4（h264/aac），故调用方 MIME 应按 video/mp4 处理。
    """
    if shutil.which("ffmpeg") is None:
        print("[media] 警告: 未安装 ffmpeg，无法压缩超大视频，将按原样发送", flush=True)
        return None
    for vf in (None, "scale=1280:-2"):
        with tempfile.TemporaryDirectory(prefix="gemini_vid_") as td:
            out = Path(td) / "compressed.mp4"
            cmd = [
                "ffmpeg", "-y", "-i", str(src),
                "-c:v", "libx264", "-preset", "fast", "-crf", "28",
                "-c:a", "aac", "-b:a", "96k",
                "-movflags", "+faststart",
            ]
            if vf:
                cmd += ["-vf", vf]
            cmd.append(str(out))
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=300)
                if r.returncode != 0:
                    continue
                if out.stat().st_size <= VIDEO_TARGET_BYTES:
                    return out.read_bytes()
            except Exception:  # noqa: BLE001 压缩失败则尝试下一档
                continue
    print(f"[media] 警告: ffmpeg 压缩后仍超限 {src}，将按原样发送", flush=True)
    return None


def as_data_uri(path: str | Path, kind: str, *, max_video_bytes: int | None = None) -> str:
    """把本地文件读成 data URI，供 Gemini 多模态消息使用。

    max_video_bytes：视频超过该字节数时用 ffmpeg 压缩兜底（仅 video 生效）；
    H3 出片等场景不传此参数，保留原始视频。
    """
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

    data = p.read_bytes()
    if kind == "video" and max_video_bytes and p.stat().st_size > max_video_bytes:
        compressed = _compress_video_bytes(p)
        if compressed is not None:
            data = compressed
            mime = "video/mp4"  # 压缩产物固定 mp4
    b64 = base64.b64encode(data).decode("ascii")
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
        # 视频超过 inlineData 上限时自动用 ffmpeg 压缩（保留原文件，仅压缩发送副本）。
        parts.append(
            {
                "type": "video_url",
                "video_url": {
                    "url": as_data_uri(p, "video", max_video_bytes=VIDEO_INLINE_LIMIT_BYTES),
                    "detail": "high",
                },
            }
        )
    for p in audios or []:
        # Gemini 多模态消息里音频需要用 `audio_url`，否则上游会按“图片”解析。
        parts.append({"type": "audio_url", "audio_url": {"url": as_data_uri(p, "audio"), "detail": "high"}})
    return parts
