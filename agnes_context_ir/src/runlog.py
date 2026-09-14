"""运行级模型调用日志。

每次 agent 运行（一条意图的完整增强/出片链路）会在 log/ 下生成一个小目录，
目录内按调用顺序记录对模型的每一次 HTTP 请求与响应，方便在服务器上排查问题。

目录结构（log/ 位于仓库根目录，已被 .gitignore 忽略）:
    log/
      run_<模式>_<时间戳>_<序号>/
        01_route_request.txt
        01_route_response.txt
        02_perceive_request.txt
        02_perceive_response.txt
        03_expand_request.txt
        03_expand_response.txt
        ...
        meta.json          # 本次运行的意图/模式/参数/结果路径
        h3_create_request.txt / h3_create_response.txt   # 出片时才有

实现方式：gemini.chat() 与 video.generate_video() 通过 log_model_call() /
log_http_call() 把每次调用落盘。是否落盘由当前线程的 contextvar 控制——
oneclick_run.py 进入每条意图前 activate(log_dir)，结束后 deactivate()。
"""

from __future__ import annotations

import contextvars
import json
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LOG_ROOT = ROOT / "log"

# 当前激活的日志目录（None = 不落盘）。用 contextvar 避免并发时串日志。
_active_dir: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "runlog_active_dir", default=None
)
# 每个 stage 的调用序号，用于给请求/响应文件编号。
_seq: contextvars.ContextVar[dict[str, int]] = contextvars.ContextVar(
    "runlog_seq", default={}
)


def activate(log_dir: Path) -> None:
    """激活本次运行的日志目录，目录不存在时自动创建。"""
    d = Path(log_dir).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    _active_dir.set(d)
    _seq.set({})


def deactivate() -> None:
    """结束本次运行，后续模型调用不再写日志。"""
    _active_dir.set(None)
    _seq.set({})


def active_dir() -> Path | None:
    """当前激活的日志目录，未激活返回 None。"""
    return _active_dir.get()


def _next_seq(stage: str) -> tuple[Path, int]:
    """返回 (当前激活目录, 该 stage 的下一个序号)。未激活时目录为 None。"""
    d = _active_dir.get()
    if d is None:
        return d, 0
    seq = _seq.get()
    n = seq.get(stage, 0) + 1
    seq[stage] = n
    _seq.set(seq)
    return d, n


def _serialize_user(user: str | list[dict[str, Any]] | Any) -> str:
    """把 OpenAI 风格多模态 content 序列化成可读文本，data URI 截断避免日志超大。"""
    if isinstance(user, str):
        return user
    if isinstance(user, list):
        lines: list[str] = []
        for item in user:
            if not isinstance(item, dict):
                lines.append(str(item))
                continue
            kind = item.get("type", "?")
            url = ""
            for key in ("image_url", "video_url", "audio_url"):
                obj = item.get(key)
                if isinstance(obj, dict):
                    url = str(obj.get("url", ""))
                    break
            if url.startswith("data:") and len(url) > 200:
                url = f"{url[:120]}…(data URI 共 {len(url)} 字符，已截断)"
            lines.append(f"[{kind}] {url}" if url else json.dumps(item, ensure_ascii=False))
        return "\n".join(lines)
    return json.dumps(user, ensure_ascii=False, default=str)


def _truncate(text: str, limit: int = 4000) -> str:
    """超长文本截断并标注总长度，避免单个响应把目录撑爆。"""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n…(共 {len(text)} 字符，已截断)"


def log_model_call(
    *,
    stage: str,
    system: str,
    user: str | list[dict[str, Any]],
    response: str,
    ok: bool = True,
) -> None:
    """记录一次 Gemini 调用：请求（system + user）与响应各写一个文件。

    stage: route / perceive / expand / elaborate / format / verify / verify_intent。
    """
    d, n = _next_seq(stage)
    if d is None:
        return
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    req_text = (
        f"# stage={stage}  time={stamp}  seq={n}\n"
        f"# ---- SYSTEM ----\n{_truncate(system, 8000)}\n"
        f"# ---- USER ----\n{_truncate(_serialize_user(user), 8000)}\n"
    )
    status = "ok" if ok else "error"
    resp_text = f"# stage={stage}  time={stamp}  seq={n}  status={status}\n{_truncate(response)}\n"
    (d / f"{n:02d}_{stage}_request.txt").write_text(req_text, encoding="utf-8")
    (d / f"{n:02d}_{stage}_response.txt").write_text(resp_text, encoding="utf-8")


def log_http_call(
    *,
    name: str,
    request: dict[str, Any] | str,
    response: str,
    ok: bool = True,
) -> None:
    """记录一次非 Gemini 的模型 HTTP 调用（如 H3 出片），成对写 request/response 文件。"""
    d, n = _next_seq(name)
    if d is None:
        return
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(request, dict):
        req = json.dumps(request, ensure_ascii=False, default=str)
    else:
        req = str(request)
    status = "ok" if ok else "error"
    (d / f"{n:02d}_{name}_request.txt").write_text(
        f"# name={name}  time={stamp}  seq={n}\n{_truncate(req, 8000)}\n",
        encoding="utf-8",
    )
    (d / f"{n:02d}_{name}_response.txt").write_text(
        f"# name={name}  time={stamp}  seq={n}  status={status}\n{_truncate(response)}\n",
        encoding="utf-8",
    )


def write_meta(meta: dict[str, Any]) -> None:
    """把本次运行的关键信息（意图/模式/参数/结果路径）写到 meta.json。"""
    d = _active_dir.get()
    if d is None:
        return
    (d / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
