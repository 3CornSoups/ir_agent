"""MiniMax H3 云端出片（/v2/video_generation）。画幅与时长只走 API，不写进 prompt。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .config import h3_settings
from .media import as_data_uri

MODES = ("t2va", "i2va", "r2va")
RESOLUTIONS = ("768P", "2K")
T2VA_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
ALL_RATIOS = ("adaptive",) + T2VA_RATIOS


def _is_url(value: str) -> bool:
    """公网 URL / mm_file / data URI 无需本地读取。"""
    parsed = urlparse(value)
    return (
        parsed.scheme in ("http", "https")
        or value.startswith("mm_file://")
        or value.startswith("data:")
    )


def _media_url(value: str, kind: str) -> str:
    """本地路径转 data URI；URL 原样返回。"""
    if _is_url(value):
        return value
    return as_data_uri(value, kind)


def build_content(
    mode: str,
    prompt: str,
    *,
    first_frame: str | None = None,
    reference_images: list[str] | None = None,
    reference_videos: list[str] | None = None,
    reference_audios: list[str] | None = None,
) -> list[dict[str, Any]]:
    """按官方 Video Generation V2 组装 content。"""
    prompt = (prompt or "").strip()
    if len(prompt) > 7000:
        print(f"[h3] 警告: prompt {len(prompt)} 字，云端上限 7000", flush=True)
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    mode = mode.lower()
    if mode == "t2va":
        return content
    if mode == "i2va":
        if not first_frame:
            raise ValueError("i2va 需要 first_frame")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _media_url(first_frame, "image")},
                "role": "first_frame",
            }
        )
        return content
    if mode == "r2va":
        imgs = reference_images or []
        vids = reference_videos or []
        auds = reference_audios or []
        if not imgs and not vids:
            raise ValueError("r2va 须至少 1 张参考图或 1 段参考视频")
        for u in imgs:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _media_url(u, "image")},
                    "role": "reference_image",
                }
            )
        for u in vids:
            content.append(
                {
                    "type": "video_url",
                    "video_url": {"url": _media_url(u, "video")},
                    "role": "reference_video",
                }
            )
        for u in auds:
            content.append(
                {
                    "type": "audio_url",
                    "audio_url": {"url": _media_url(u, "audio")},
                    "role": "reference_audio",
                }
            )
        return content
    raise ValueError(f"未知模式: {mode}")


def resolve_ratio(mode: str, ratio: str | None) -> str:
    """t2va 用具体比例；i2va/r2va 默认 adaptive。"""
    mode = mode.lower()
    if mode == "t2va":
        r = ratio or "16:9"
        if r not in T2VA_RATIOS:
            raise ValueError(f"t2va ratio 可选: {', '.join(T2VA_RATIOS)}")
        return r
    if not ratio:
        return "adaptive"
    if ratio not in ALL_RATIOS:
        raise ValueError(f"非法 ratio: {ratio}")
    return ratio


def _parse_response(resp: requests.Response) -> dict[str, Any]:
    """解析 MiniMax JSON；失败抛 RuntimeError。"""
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"HTTP {resp.status_code}, 非 JSON: {resp.text[:500]}") from exc
    if resp.status_code >= 400:
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            raise RuntimeError(
                f"HTTP {resp.status_code}: type={err.get('type')} message={err.get('message')}"
            )
        raise RuntimeError(f"HTTP {resp.status_code}: {json.dumps(data, ensure_ascii=False)}")
    if isinstance(data, dict) and data.get("type") == "error":
        err = data.get("error") or {}
        raise RuntimeError(f"API error: {err.get('message')}")
    return data


def generate_video(
    mode: str,
    prompt: str,
    *,
    duration: int,
    ratio: str | None = None,
    resolution: str | None = None,
    first_frame: str | None = None,
    reference_images: list[str] | None = None,
    reference_videos: list[str] | None = None,
    reference_audios: list[str] | None = None,
    output: str | Path | None = None,
    wait: bool = True,
) -> dict[str, Any]:
    """
    提交 H3 出片任务；默认轮询并下载。

    Returns:
        含 task_id；成功时含 video_path / url
    """
    cfg = h3_settings()
    # 本地推理可能不需要鉴权；通过 skip_auth 或配置了本地服务来允许 api_key 为空。
    if not cfg.get("api_key") and not cfg.get("skip_auth"):
        raise RuntimeError(
            "缺少 MiniMax API Key：设 MINIMAX_API_KEY 或填写 configs/h3.yaml，"
            "或设置 skip_auth=true 以使用本地不鉴权服务。"
        )
    if duration < 4 or duration > 15:
        raise ValueError("duration 须在 4~15 秒")
    res = resolution or cfg["default_resolution"]
    if res not in RESOLUTIONS:
        raise ValueError(f"resolution 可选: {', '.join(RESOLUTIONS)}")
    resolved_ratio = resolve_ratio(mode, ratio)
    content = build_content(
        mode,
        prompt,
        first_frame=first_frame,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
    )
    session = requests.Session()
    # content-type 固定；Authorization 按需注入。
    session.headers.update({"Content-Type": "application/json"})
    if cfg.get("api_key") and not cfg.get("skip_auth"):
        session.headers.update({"Authorization": f"Bearer {cfg['api_key']}"})
    body: dict[str, Any] = {
        "model": cfg["model"],
        "content": content,
        "resolution": res,
        "duration": int(duration),
        "ratio": resolved_ratio,
    }
    create_url = f"{cfg['base_url']}{cfg['generate_path']}"
    resp = session.post(create_url, json=body, timeout=cfg["timeout_sec"])
    data = _parse_response(resp)
    task_id = data.get("task_id")
    result: dict[str, Any] = {
        "ratio": resolved_ratio,
        "resolution": res,
        "duration": int(duration),
    }

    # 兼容：部分本地服务可能直接在创建阶段返回最终 url，而不走 task_id 轮询。
    if not task_id:
        content_obj = data.get("content") or {}
        url = content_obj.get("url") if isinstance(content_obj, dict) else None
        url = url or data.get("url")
        if url:
            result["url"] = url
            result["task"] = {"status": "succeeded", "content": content_obj}
        else:
            raise RuntimeError(f"未返回 task_id: {json.dumps(data, ensure_ascii=False)}")
    else:
        result["task_id"] = str(task_id)
    if not wait:
        return result

    deadline = time.time() + float(cfg["poll_timeout_sec"])
    last_status = None
    query_url = f"{cfg['base_url']}{cfg['query_path_template'].format(task_id=task_id)}"
    task: dict[str, Any] = {}
    # 若创建阶段已给出 url，直接下载并返回。
    if not task_id and result.get("url"):
        url = result.get("url")
        content_obj = result.get("task", {}).get("content") if isinstance(result.get("task"), dict) else {}
        # 保持 task/content 字段结构一致性。
        result["task"] = {"status": "succeeded", "content": content_obj or {}}
        task = result["task"]  # type: ignore[assignment]
    else:
        while time.time() < deadline:
            q = session.get(query_url, timeout=cfg["timeout_sec"])
            payload = _parse_response(q)
            task = payload.get("task") if isinstance(payload.get("task"), dict) else payload
            status = task.get("status")
            if status != last_status:
                print(f"[h3] task_id={task_id} status={status}", flush=True)
                last_status = status
            if status == "succeeded":
                break
            if status in ("failed", "cancelled"):
                err = task.get("error") or {}
                raise RuntimeError(f"出片 {status}: {err.get('message') or err}")
            time.sleep(float(cfg["poll_interval_sec"]))
        else:
            raise TimeoutError(f"出片超时: task_id={task_id}")

    content_obj = task.get("content") or {}
    url = content_obj.get("url") if isinstance(content_obj, dict) else None
    result["url"] = url
    result["task"] = task
    if output and url:
        dest = Path(output).expanduser().resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        # 预签名 URL 不能带 JSON Content-Type
        with requests.get(url, stream=True, timeout=600) as dl:
            dl.raise_for_status()
            with dest.open("wb") as f:
                for chunk in dl.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        f.write(chunk)
        result["video_path"] = str(dest)
        print(f"[h3] saved {dest}", flush=True)
    return result
