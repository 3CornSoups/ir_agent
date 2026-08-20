#!/usr/bin/env python3
"""官方 H3-Context-IR 与本地 agent 提示词对照工具。

对同一组「意图 + 参考素材」，分别用本仓库 enhance 管线与官方 H3-Context-IR API
生成提示词，输出并排对照报告（结构校验 / 估算 token / 差异 diff），供后续分别
出片做人工质量对比。

用法：
    python3 scripts/compare_context_ir.py -m t2va --intent "一只橘猫在窗台晒太阳"
    python3 scripts/compare_context_ir.py -m r2va --intent "保持人设走路" \
        --ref-image face.png --ref-video walk.mp4
    python3 scripts/compare_context_ir.py -m t2va --intent "..." --official-key 您的key \
        --official-base-url https://api.minimaxi.com

未提供官方 key 时只生成本地提示词，official 部分标记为 skipped。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.pipeline import enhance  # noqa: E402
from src.video import build_content  # noqa: E402
from src.verify import verify_prompt  # noqa: E402

# 官方 Context-IR 接口（Endpoint 见 MiniMax-H3 README / API 文档）
OFFICIAL_CREATE_PATH = "/video-generation-v2-h3-context-ir"
OFFICIAL_QUERY_TEMPLATE = "/v2/query/video_generation/{task_id}"


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（英文按 4 字符/token，中文按 1.5 字符/token）。"""
    text = text or ""
    ascii_len = sum(1 for c in text if ord(c) < 128)
    other = len(text) - ascii_len
    return ascii_len // 4 + int(other / 1.5)


def call_official_context_ir(
    cfg: dict,
    mode: str,
    prompt_text: str,
    *,
    duration: int,
    ratio: str | None = None,
    first_frame: str | None = None,
    last_frame: str | None = None,
    reference_images: list[str] | None = None,
    reference_videos: list[str] | None = None,
    reference_audios: list[str] | None = None,
) -> dict:
    """调用官方 H3-Context-IR 异步任务，返回 {prompt, task_id, tokens}。"""
    base = cfg["base_url"].rstrip("/")
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    content = build_content(
        mode,
        prompt_text,
        first_frame=first_frame,
        last_frame=last_frame,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
    )
    body = {
        "model": cfg["model"],
        "content": content,
        "duration": int(duration),
    }
    if ratio:
        body["ratio"] = ratio

    resp = requests.post(
        f"{base}{OFFICIAL_CREATE_PATH}",
        json=body,
        headers=headers,
        timeout=cfg["timeout_sec"],
    )
    resp.raise_for_status()
    data = resp.json()
    task_id = str(data.get("task_id") or "")
    if not task_id:
        # 部分网关直接返回 content.prompt
        prompt = (data.get("content") or {}).get("prompt") or data.get("prompt") or ""
        if prompt:
            return {"prompt": prompt, "task_id": "", "tokens": _estimate_tokens(prompt)}
        raise RuntimeError(f"官方 Context-IR 未返回 task_id: {json.dumps(data, ensure_ascii=False)[:500]}")

    query_url = f"{base}{OFFICIAL_QUERY_TEMPLATE.format(task_id=task_id)}"
    deadline = time.time() + cfg["poll_timeout_sec"]
    last_status = None
    while time.time() < deadline:
        q = requests.get(query_url, headers=headers, timeout=cfg["timeout_sec"])
        q.raise_for_status()
        payload = q.json()
        task = payload.get("task") if isinstance(payload.get("task"), dict) else payload
        status = task.get("status")
        if status != last_status:
            print(f"[official] task_id={task_id} status={status}", flush=True)
            last_status = status
        if status == "succeeded":
            content_obj = task.get("content") or {}
            prompt = content_obj.get("prompt") or (content_obj.get("content") or {}).get("prompt") or ""
            if not prompt:
                raise RuntimeError("官方 Context-IR 成功但缺少 content.prompt")
            return {"prompt": prompt, "task_id": task_id, "tokens": _estimate_tokens(prompt)}
        if status in ("failed", "cancelled"):
            err = task.get("error") or {}
            raise RuntimeError(f"官方 Context-IR {status}: {err.get('message') or err}")
        time.sleep(cfg["poll_interval_sec"])
    raise TimeoutError(f"官方 Context-IR 轮询超时: task_id={task_id}")


def main() -> int:
    """解析参数、生成本地与官方提示词并写对照报告。"""
    p = argparse.ArgumentParser(description="官方 Context-IR vs 本地 agent 提示词对照")
    p.add_argument("-m", "--mode", required=True, help="t2va / i2va / fl2va / l2va / r2va")
    p.add_argument("--intent", default="", help="短意图文本")
    p.add_argument("--intent-file", type=Path, help="从文件读短意图")
    p.add_argument("--first-frame", help="i2va / fl2va 首帧")
    p.add_argument("--last-frame", help="fl2va / l2va 尾帧")
    p.add_argument("--ref-image", action="append", default=[], help="r2va 参考图，可重复")
    p.add_argument("--ref-video", action="append", default=[], help="r2va 参考视频，可重复")
    p.add_argument("--ref-audio", action="append", default=[], help="r2va 参考音频，可重复")
    p.add_argument("--duration", type=int, default=None, help="出片秒数 4–15")
    p.add_argument("--ratio", default=None, help="画幅（仅官方请求使用；本地不进 prompt）")
    p.add_argument("--out-dir", type=Path, default=None, help="报告输出目录（默认 runs/compare_<时间>）")
    p.add_argument("--official-key", default="", help="官方 MiniMax API key（缺省读 MINIMAX_API_KEY）")
    p.add_argument("--official-base-url", default="https://api.minimaxi.com", help="官方 API base URL")
    p.add_argument("--official-skip", action="store_true", help="跳过官方调用，只生成本地提示词")
    args = p.parse_args()

    intent = args.intent.strip()
    if args.intent_file:
        intent = args.intent_file.read_text(encoding="utf-8")
    if not intent.strip():
        p.error("请提供 --intent 或 --intent-file")

    out_dir = args.out_dir or ROOT / "runs" / f"compare_{time.strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    rec = enhance(
        args.mode,
        intent,
        first_frame=args.first_frame,
        last_frame=args.last_frame,
        reference_images=args.ref_image or None,
        reference_videos=args.ref_video or None,
        reference_audios=args.ref_audio or None,
        duration=args.duration,
        out_dir=out_dir,
    )
    local_prompt = rec["prompt"]
    if args.mode == "r2va":
        local_issues = verify_prompt(
            args.mode,
            local_prompt,
            duration=rec["duration"],
            images=len(args.ref_image),
            videos=len(args.ref_video),
            audios=len(args.ref_audio),
            intent=rec.get("intent", ""),
        )
    elif args.mode in ("i2va", "fl2va", "l2va"):
        frames = [f for f in (args.first_frame, args.last_frame) if f]
        local_issues = verify_prompt(
            args.mode,
            local_prompt,
            duration=rec["duration"],
            images=len(frames),
            intent=rec.get("intent", ""),
        )
    else:
        local_issues = verify_prompt(
            args.mode, local_prompt, duration=rec["duration"], intent=rec.get("intent", "")
        )

    official_prompt = None
    official_meta: dict = {"skipped": True, "reason": "官方调用被跳过（--official-skip 或无 key）"}
    if not args.official_skip:
        api_key = args.official_key or ""
        official_meta = {}
        if not api_key:
            official_meta = {"skipped": True, "reason": "缺少官方 key（--official-key 或 MINIMAX_API_KEY）"}
        else:
            try:
                result = call_official_context_ir(
                    {
                        "api_key": api_key,
                        "base_url": args.official_base_url,
                        "model": "MiniMax-H3",
                        "timeout_sec": 120,
                        "poll_interval_sec": 5,
                        "poll_timeout_sec": 1800,
                    },
                    args.mode,
                    intent,
                    duration=rec["duration"],
                    ratio=args.ratio,
                    first_frame=args.first_frame,
                    last_frame=args.last_frame,
                    reference_images=args.ref_image or None,
                    reference_videos=args.ref_video or None,
                    reference_audios=args.ref_audio or None,
                )
                official_prompt = result["prompt"]
                official_meta = {
                    "skipped": False,
                    "task_id": result["task_id"],
                    "tokens": result["tokens"],
                }
            except Exception as exc:  # noqa: BLE001 官方调用失败不阻断本地报告
                official_meta = {"skipped": True, "error": str(exc)}

    (out_dir / "local_prompt.txt").write_text(local_prompt, encoding="utf-8")
    if official_prompt is not None:
        (out_dir / "official_prompt.txt").write_text(official_prompt, encoding="utf-8")

    lines = ["# 官方 Context-IR vs 本地 Agent 提示词对照", ""]
    lines.append(f"- mode: {args.mode}")
    lines.append(f"- intent: {intent.strip()}")
    lines.append(f"- duration: {rec['duration']}s")
    lines.append(f"- 本地 agent：tokens≈{_estimate_tokens(local_prompt)}，结构校验 errors={len([i for i in local_issues if i.severity == 'error'])}, warnings={len([i for i in local_issues if i.severity == 'warning'])}")
    if official_prompt is not None:
        lines.append(f"- 官方 Context-IR：tokens≈{official_meta.get('tokens', _estimate_tokens(official_prompt))}")
    else:
        lines.append(f"- 官方 Context-IR：{official_meta.get('reason', 'skipped')}")
    lines.append("")
    lines.append("## 本地 Agent 提示词")
    lines.append("```")
    lines.append(local_prompt.strip())
    lines.append("```")
    lines.append("")
    if official_prompt is not None:
        lines.append("## 官方 Context-IR 提示词")
        lines.append("```")
        lines.append(official_prompt.strip())
        lines.append("```")
        lines.append("")

    summary = {
        "mode": args.mode,
        "intent": intent.strip(),
        "duration": rec["duration"],
        "local": {
            "tokens_est": _estimate_tokens(local_prompt),
            "verify": [{"code": i.code, "severity": i.severity, "message": i.message} for i in local_issues],
        },
        "official": official_meta,
    }
    (out_dir / "compare.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "compare_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[compare] 报告 → {out_dir / 'compare_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
