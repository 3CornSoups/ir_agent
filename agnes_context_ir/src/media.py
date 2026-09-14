"""本地路径 / http(s) URL / data URI → Gemini 多模态消息部件。"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import unquote, unquote_to_bytes, urlparse

logger = logging.getLogger(__name__)

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
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".flac": "audio/flac",
}
AUDIO_FORMAT_BY_EXT = {
    ".mp3": "mp3",
    ".wav": "wav",
    ".wave": "wav",
    ".m4a": "m4a",
    ".aac": "aac",
    ".ogg": "ogg",
    ".oga": "ogg",
    ".flac": "flac",
    ".mp4": "mp4",
}
AUDIO_FORMAT_BY_MIME = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mp4": "mp4",
    "audio/m4a": "m4a",
    "audio/aac": "aac",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
}
DEFAULT_AUDIO_FORMAT = "mp3"

# Gemini 原生端点 inlineData 上限（Blob data，字节）；超过即触发 ffmpeg 压缩兜底。
VIDEO_INLINE_LIMIT_BYTES = 20 * 1024 * 1024
# 压缩目标：留出 base64 放大（约 4/3 倍）余量，保证压缩产物 + base64 后仍落在上限内。
VIDEO_TARGET_BYTES = 15 * 1024 * 1024

_DOWNLOAD_CHUNK = 64 * 1024


def _mime(path: Path, table: dict[str, str], fallback: str) -> str:
    ext = path.suffix.lower()
    if ext in table:
        return table[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or fallback


def is_http_url(value: str) -> bool:
    raw = (value or "").strip()
    return raw.startswith(("http://", "https://"))


def is_data_uri(value: str) -> bool:
    return (value or "").strip().lower().startswith("data:")


def is_forwardable_media_ref(value: str) -> bool:
    raw = (value or "").strip()
    return is_http_url(raw) or is_data_uri(raw) or bool(raw)


def _parse_data_uri(raw: str) -> tuple[str | None, str, bool]:
    header, sep, body = raw.partition(",")
    if sep != "," or not header.lower().startswith("data:"):
        raise ValueError("not a data URI")
    meta = header[5:]
    parts = [p for p in meta.split(";") if p]
    mime: str | None = None
    is_base64 = False
    for index, part in enumerate(parts):
        if part.lower() == "base64":
            is_base64 = True
        elif index == 0 and "/" in part:
            mime = part
    return mime, body, is_base64


def _infer_audio_format(*, url: str = "", mime: str | None = None) -> str:
    if mime:
        mime_main = mime.split(";", 1)[0].strip().lower()
        mapped = AUDIO_FORMAT_BY_MIME.get(mime_main)
        if mapped:
            return mapped
        if mime_main.startswith("audio/"):
            subtype = mime_main.split("/", 1)[1]
            if subtype in {"mp3", "wav", "m4a", "aac", "ogg", "flac", "mp4"}:
                return subtype
    path = unquote(urlparse(url).path).lower()
    ext = os.path.splitext(path)[1]
    return AUDIO_FORMAT_BY_EXT.get(ext, DEFAULT_AUDIO_FORMAT)


def _suffix_for_kind(kind: str, mime: str | None = None, url: str = "") -> str:
    if mime:
        mime_main = mime.split(";", 1)[0].strip().lower()
        if mime_main == "image/jpeg":
            return ".jpg"
        if mime_main == "image/png":
            return ".png"
        if mime_main == "image/webp":
            return ".webp"
        if mime_main.startswith("video/"):
            return ".mp4"
        if mime_main.startswith("audio/"):
            return ".mp3"
    path = unquote(urlparse(url).path).lower()
    ext = os.path.splitext(path)[1]
    if ext:
        return ext
    return {
        "image": ".jpg",
        "video": ".mp4",
        "audio": ".mp3",
    }[kind]


def _download_bytes_once(
    url: str,
    *,
    timeout_s: float,
    max_bytes: int,
) -> tuple[bytes, str | None]:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_s) as resp:
        ctype = resp.headers.get("Content-Type")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = resp.read(_DOWNLOAD_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise OSError(
                    f"download exceeds max size ({total} > {max_bytes}): {url}"
                )
            chunks.append(chunk)
    return b"".join(chunks), ctype


def _download_retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code in {408, 429}
    if isinstance(exc, urllib.error.URLError):
        return True
    if isinstance(exc, (TimeoutError, OSError)):
        return "exceeds max size" not in str(exc).lower()
    return False


def _download_bytes(
    url: str,
    *,
    timeout_s: float,
    max_bytes: int,
    max_retries: int = 0,
    retry_backoff_s: float = 1.0,
) -> tuple[bytes, str | None]:
    attempts = max(1, int(max_retries) + 1)
    last_exc: BaseException | None = None
    for attempt in range(attempts):
        if attempt:
            backoff = max(0.0, float(retry_backoff_s)) * attempt
            logger.warning(
                "PE media download retry %s/%s url=%s backoff=%.1fs err=%s",
                attempt,
                attempts - 1,
                url,
                backoff,
                last_exc,
            )
            if backoff:
                time.sleep(backoff)
        try:
            return _download_bytes_once(
                url,
                timeout_s=timeout_s,
                max_bytes=max_bytes,
            )
        except Exception as exc:
            last_exc = exc
            if not _download_retryable(exc) or attempt + 1 >= attempts:
                break

    if isinstance(last_exc, urllib.error.HTTPError):
        raise OSError(
            f"download failed HTTP {last_exc.code}: {url}"
        ) from last_exc
    if isinstance(last_exc, urllib.error.URLError):
        raise OSError(f"download failed: {url}: {last_exc}") from last_exc
    if isinstance(last_exc, TimeoutError):
        raise OSError(f"download timed out: {url}") from last_exc
    if isinstance(last_exc, OSError):
        raise last_exc
    raise OSError(f"download failed: {url}: {last_exc}") from last_exc


def _download_options(media_cfg: dict[str, Any]) -> dict[str, float | int]:
    return {
        "timeout_s": float(media_cfg["download_timeout_sec"]),
        "max_retries": int(media_cfg.get("download_max_retries", 0)),
        "retry_backoff_s": float(
            media_cfg.get("download_retry_backoff_sec", 1.0)
        ),
    }


def _write_temp_bytes(data: bytes, suffix: str) -> Path:
    fd, name = tempfile.mkstemp(suffix=suffix, prefix="pe_media_")
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return Path(name)


def _data_uri_to_temp(uri: str, kind: str) -> Path:
    mime, body, is_base64 = _parse_data_uri(uri)
    if is_base64:
        data = base64.b64decode(body)
    else:
        data = unquote_to_bytes(body)
    if not data:
        raise ValueError("empty data URI")
    suffix = _suffix_for_kind(kind, mime=mime)
    return _write_temp_bytes(data, suffix)


def _download_url_to_temp(url: str, kind: str, media_cfg: dict[str, Any]) -> Path:
    max_bytes = int(media_cfg["max_download_bytes"])
    if kind == "image":
        max_bytes = min(max_bytes, int(media_cfg["max_image_bytes"]))
    data, ctype = _download_bytes(
        url,
        max_bytes=max_bytes,
        **_download_options(media_cfg),
    )
    if not data:
        raise OSError(f"empty download: {url}")
    suffix = _suffix_for_kind(kind, mime=ctype, url=url)
    return _write_temp_bytes(data, suffix)


def materialize_media_path(
    ref: str,
    kind: str,
    media_cfg: dict[str, Any],
) -> Path:
    """把本地路径 / URL / data URI 落成可读本地文件（临时文件或原路径）。"""
    raw = (ref or "").strip()
    if not raw:
        raise ValueError("empty media reference")
    if is_data_uri(raw):
        return _data_uri_to_temp(raw, kind)
    if is_http_url(raw):
        return _download_url_to_temp(raw, kind, media_cfg)
    path = Path(raw).expanduser()
    if path.is_file():
        return path.resolve()
    raise FileNotFoundError(f"媒体不存在或不可访问: {raw}")


def cleanup_materialized_path(path: Path, original_ref: str) -> None:
    """Delete files created for URL/data refs; never delete caller-owned paths."""
    if is_http_url(original_ref) or is_data_uri(original_ref):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("清理临时媒体失败: %s", path)


def _compress_video_bytes(src: Path) -> bytes | None:
    if shutil.which("ffmpeg") is None:
        logger.warning("ffmpeg 未安装，无法压缩超大视频，将按原样发送")
        return None
    for vf in (None, "scale=1280:-2"):
        with tempfile.TemporaryDirectory(prefix="gemini_vid_") as td:
            out = Path(td) / "compressed.mp4"
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "28",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-movflags",
                "+faststart",
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
            except Exception:  # noqa: BLE001
                continue
    logger.warning("ffmpeg 压缩后仍超限 %s，将按原样发送", src)
    return None


def as_data_uri(path: str | Path, kind: str, *, max_video_bytes: int | None = None) -> str:
    """把本地文件读成 data URI，供 Gemini 多模态消息使用。"""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"媒体不存在: {p}")
    if kind == "image":
        mime = _mime(p, IMAGE_MIME, "image/jpeg")
        try:
            from PIL import Image, ImageFile
            import io

            ImageFile.LOAD_TRUNCATED_IMAGES = True
            with Image.open(p) as img:
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                if max(img.size) > 1280 or p.stat().st_size > 300_000:
                    img.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                data = buf.getvalue()
                mime = "image/jpeg"
        except Exception:
            data = p.read_bytes()
    elif kind == "video":
        mime = _mime(p, VIDEO_MIME, "video/mp4")
        data = p.read_bytes()
    elif kind == "audio":
        mime = _mime(p, AUDIO_MIME, "audio/mpeg")
        data = p.read_bytes()
    else:
        raise ValueError(f"未知 kind: {kind}")

    if kind == "video" and max_video_bytes and p.stat().st_size > max_video_bytes:
        compressed = _compress_video_bytes(p)
        if compressed is not None:
            data = compressed
            mime = "video/mp4"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _input_audio_part(data_b64: str, fmt: str) -> dict[str, Any]:
    return {
        "type": "input_audio",
        "input_audio": {"data": data_b64, "format": fmt},
    }


def _build_image_part(
    ref: str,
    *,
    protocol: str,
    media_cfg: dict[str, Any],
    image_detail: str | None = "high",
) -> dict[str, Any] | None:
    raw = (ref or "").strip()
    if not raw:
        return None

    def _image_url_payload(url: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"url": url}
        if image_detail:
            payload["detail"] = image_detail
        return {"type": "image_url", "image_url": payload}

    if protocol == "openai" and (is_http_url(raw) or is_data_uri(raw)):
        return _image_url_payload(raw)
    path: Path | None = None
    try:
        path = materialize_media_path(raw, "image", media_cfg)
        return _image_url_payload(as_data_uri(path, "image"))
    except (OSError, ValueError, FileNotFoundError) as exc:
        logger.warning("跳过图片 %s: %s", raw, exc)
        return None
    finally:
        if path is not None:
            cleanup_materialized_path(path, raw)


def _build_video_part(
    ref: str,
    *,
    protocol: str,
    media_cfg: dict[str, Any],
) -> dict[str, Any] | None:
    """组装视频消息部件。

    OpenAI 兼容网关通常只认 ``image_url``（Cloudsway/agrouter 对 https 视频也如此），
    因此 openai 协议下：http(s)/data URI 直传；本地路径先压成 data URI 再塞进 image_url。
    native 协议仍用 ``video_url``，由 ``_to_native_parts`` 转 inlineData。
    """
    raw = (ref or "").strip()
    if not raw:
        return None

    # OpenAI 兼容层：统一走 image_url，避免本地路径误发 video_url 被网关丢弃。
    if protocol == "openai":
        if is_http_url(raw) or is_data_uri(raw):
            return {"type": "image_url", "image_url": {"url": raw}}
        path: Path | None = None
        try:
            path = materialize_media_path(raw, "video", media_cfg)
            data_uri = as_data_uri(
                path,
                "video",
                max_video_bytes=VIDEO_INLINE_LIMIT_BYTES,
            )
            return {"type": "image_url", "image_url": {"url": data_uri}}
        except (OSError, ValueError, FileNotFoundError) as exc:
            logger.warning("跳过视频 %s: %s", raw, exc)
            return None
        finally:
            if path is not None:
                cleanup_materialized_path(path, raw)

    path = None
    try:
        path = materialize_media_path(raw, "video", media_cfg)
        return {
            "type": "video_url",
            "video_url": {
                "url": as_data_uri(
                    path,
                    "video",
                    max_video_bytes=VIDEO_INLINE_LIMIT_BYTES,
                ),
                "detail": "high",
            },
        }
    except (OSError, ValueError, FileNotFoundError) as exc:
        logger.warning("跳过视频 %s: %s", raw, exc)
        return None
    finally:
        if path is not None:
            cleanup_materialized_path(path, raw)


def _build_audio_part(
    ref: str,
    *,
    protocol: str,
    media_cfg: dict[str, Any],
    download_cache: dict[str, tuple[bytes, str | None] | None] | None = None,
) -> dict[str, Any] | None:
    raw = (ref or "").strip()
    if not raw:
        return None
    if is_data_uri(raw):
        try:
            mime, body, is_base64 = _parse_data_uri(raw)
            data_b64 = (
                "".join(body.split())
                if is_base64
                else base64.b64encode(unquote_to_bytes(body)).decode("ascii")
            )
            if not data_b64:
                raise ValueError("empty audio data URI")
            fmt = _infer_audio_format(mime=mime)
            if protocol == "openai":
                return _input_audio_part(data_b64, fmt)
            data_uri = f"data:{mime or 'audio/mpeg'};base64,{data_b64}"
            return {"type": "audio_url", "audio_url": {"url": data_uri, "detail": "high"}}
        except (ValueError, OSError) as exc:
            logger.warning("跳过音频 data URI: %s", exc)
            return None
    if is_http_url(raw):
        try:
            max_bytes = min(
                int(media_cfg["max_download_bytes"]),
                int(media_cfg["max_download_bytes"]),
            )
            if download_cache is not None and raw in download_cache:
                cached = download_cache[raw]
                if cached is None:
                    return None
                data, ctype = cached
            else:
                try:
                    data, ctype = _download_bytes(
                        raw,
                        max_bytes=max_bytes,
                        **_download_options(media_cfg),
                    )
                except (OSError, ValueError):
                    if download_cache is not None:
                        download_cache[raw] = None
                    raise
                if download_cache is not None:
                    download_cache[raw] = (data, ctype)
            if not data:
                raise OSError("empty audio download")
            fmt = _infer_audio_format(url=raw, mime=ctype)
            data_b64 = base64.b64encode(data).decode("ascii")
            if protocol == "openai":
                return _input_audio_part(data_b64, fmt)
            mime = (ctype or "audio/mpeg").split(";", 1)[0].strip()
            data_uri = f"data:{mime};base64,{data_b64}"
            return {"type": "audio_url", "audio_url": {"url": data_uri, "detail": "high"}}
        except (OSError, ValueError) as exc:
            logger.warning("跳过音频 URL %s: %s", raw, exc)
            return None
    try:
        path = materialize_media_path(raw, "audio", media_cfg)
        data_uri = as_data_uri(path, "audio")
        if protocol == "openai":
            mime, body, is_base64 = _parse_data_uri(data_uri)
            data_b64 = "".join(body.split()) if is_base64 else body
            return _input_audio_part(data_b64, _infer_audio_format(mime=mime))
        return {"type": "audio_url", "audio_url": {"url": data_uri, "detail": "high"}}
    except (OSError, ValueError, FileNotFoundError) as exc:
        logger.warning("跳过音频 %s: %s", raw, exc)
        return None


def user_parts(
    text: str,
    *,
    images: list[str] | None = None,
    videos: list[str] | None = None,
    audios: list[str] | None = None,
    protocol: str | None = None,
    media_cfg: dict[str, Any] | None = None,
    download_cache: dict[str, tuple[bytes, str | None] | None] | None = None,
) -> list[dict]:
    """拼 Gemini USER content：文本 + 图/视频/音频（本地路径 / URL / data URI）。"""
    from .config import gemini_settings, media_settings

    gcfg = gemini_settings()
    proto = (protocol or gcfg.get("protocol") or "native").strip().lower()
    mcfg = media_cfg or media_settings()

    parts: list[dict] = [{"type": "text", "text": text}]
    skipped: list[str] = []

    image_refs = [ref for ref in (images or []) if (ref or "").strip()]
    # Vertex/Gemini: HIGH detail only allowed for a single image per request.
    image_detail = "high" if len(image_refs) == 1 else None

    for ref in image_refs:
        part = _build_image_part(
            ref,
            protocol=proto,
            media_cfg=mcfg,
            image_detail=image_detail,
        )
        if part is None:
            skipped.append("image")
        else:
            parts.append(part)

    for ref in videos or []:
        part = _build_video_part(ref, protocol=proto, media_cfg=mcfg)
        if part is None:
            skipped.append("video")
        else:
            parts.append(part)

    audio_refs = list(audios or [])

    def build_audio(ref: str) -> dict[str, Any] | None:
        return _build_audio_part(
            ref,
            protocol=proto,
            media_cfg=mcfg,
            download_cache=download_cache,
        )

    unique_refs = list(dict.fromkeys(audio_refs))
    if len(unique_refs) > 1:
        with ThreadPoolExecutor(max_workers=min(3, len(unique_refs))) as pool:
            audio_by_ref = dict(zip(unique_refs, pool.map(build_audio, unique_refs)))
    else:
        audio_by_ref = {
            ref: build_audio(ref)
            for ref in unique_refs
        }

    for ref in audio_refs:
        part = audio_by_ref[ref]
        if part is None:
            skipped.append("audio")
        else:
            parts.append(part)

    if skipped:
        logger.warning("跳过不可用的媒体类型: %s", ",".join(skipped))
    return parts
