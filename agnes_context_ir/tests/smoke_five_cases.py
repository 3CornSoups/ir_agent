#!/usr/bin/env python3
"""跑 5 条短意图冒烟：覆盖禁言/补台词/明确对白/运镜 + t2va/i2va。"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.pipeline import enhance  # noqa: E402

ASSETS = ROOT / "tests" / "assets"
OUT_ROOT = ROOT / "runs" / f"smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _configure_env() -> None:
    """为冒烟配置 agrouter Gemini（可用环境变量覆盖）。"""
    os.environ.setdefault(
        "AGNES_GEMINI_API_KEY",
        os.environ.get("AGNES_GEMINI_API_KEY", ""),
    )
    os.environ.setdefault(
        "AGNES_GEMINI_API_URL",
        "https://agrouter-ng-test.kiwiar.com/v1/chat/completions",
    )
    os.environ.setdefault("AGNES_GEMINI_MODEL", "gemini-3.1-flash-lite")
    os.environ.setdefault("AGNES_GEMINI_TEXT_MODEL", "gemini-3.1-flash-lite")
    os.environ.setdefault("AGNES_CONTEXT_IR_GEMINI_PROTOCOL", "openai")
    os.environ.setdefault("AGNES_GEMINI_ENABLE_THINKING", "false")
    os.environ.setdefault("AGNES_GEMINI_TIMEOUT_S", "180")


def _has_d_tag(prompt: str) -> bool:
    """判断终稿是否含对白标签。"""
    return bool(re.search(r"<d>\s*\[[^\]]+\]", prompt or "", re.I))


def _camera_hint(prompt: str) -> str:
    """粗提运镜相关片段，便于人工扫一眼。"""
    text = prompt or ""
    keys = (
        "static",
        "push",
        "pull",
        "pan",
        "track",
        "handheld",
        "whip",
        "fixed",
        "camera",
        "slow",
    )
    hits = []
    lower = text.lower()
    for k in keys:
        if k in lower:
            hits.append(k)
    return ",".join(hits) if hits else "(no obvious camera keywords)"


CASES: list[dict] = [
    {
        "id": "01_t2va_no_speech_static",
        "mode": "t2va",
        "duration": 5,
        "intent": (
            "约5秒。清晨公园里，一位穿米色风衣的女孩独自坐在长椅上看书。"
            "全片不要说话、无对白。固定机位，中景，不要推拉摇移，不要切镜。"
        ),
        "expect": {"no_dialogue": True, "camera": "static"},
    },
    {
        "id": "02_t2va_chat_missing_lines",
        "mode": "t2va",
        "duration": 6,
        "intent": (
            "约6秒。咖啡馆里一对朋友面对面聊天，气氛轻松。"
            "用户忘了写具体台词。镜头缓慢小幅推进，单镜头。"
        ),
        "expect": {"has_dialogue": True, "camera": "push"},
    },
    {
        "id": "03_t2va_verbatim_dialogue_pan",
        "mode": "t2va",
        "duration": 5,
        "intent": (
            "约5秒。雨夜公交站，女孩撑伞对男孩说：“末班车还有三分钟。”"
            "男孩点头。镜头水平缓慢横移（pan），幅度小，速度慢。不要花哨甩镜。"
        ),
        "expect": {"has_dialogue": True, "must_contain": "末班车还有三分钟"},
    },
    {
        "id": "04_i2va_follow_and_talk",
        "mode": "i2va",
        "duration": 5,
        "first_frame": str(ASSETS / "cafe_woman.jpg"),
        "intent": (
            "约5秒。以首帧人物为起点，她抬起头对画外友人打招呼，说：“你来啦。”"
            "镜头轻柔跟拍（tracking），保持人物出画不丢，不要乱切。"
        ),
        "expect": {"has_dialogue": True, "must_contain": "你来啦"},
    },
    {
        "id": "05_i2va_silent_restrained",
        "mode": "i2va",
        "duration": 5,
        "first_frame": str(ASSETS / "library.jpg"),
        "intent": (
            "约5秒。图书馆安静角落，人物低头翻书，全片禁止说话、不要任何对白。"
            "默认克制运镜：固定或极小幅缓慢推进即可，禁止甩镜和手持晃动。"
        ),
        "expect": {"no_dialogue": True, "camera": "restrained"},
    },
]


def _check(case: dict, prompt: str) -> list[str]:
    """按期望做轻量断言，返回失败原因列表。"""
    fails: list[str] = []
    exp = case.get("expect") or {}
    if exp.get("no_dialogue") and _has_d_tag(prompt):
        fails.append("期望无对白，但出现了 <d>")
    if exp.get("has_dialogue") and not _has_d_tag(prompt):
        fails.append("期望有对白 <d>，但未出现")
    must = exp.get("must_contain")
    if must and must not in (prompt or ""):
        fails.append(f"期望保留原文台词片段：{must!r}")
    return fails


def main() -> int:
    """执行全部冒烟用例并写汇总。"""
    _configure_env()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    summary: list[dict] = []

    print(f"assets={ASSETS}")
    print(f"out={OUT_ROOT}")
    print(f"model={os.environ.get('AGNES_GEMINI_MODEL')}")
    print(f"protocol={os.environ.get('AGNES_CONTEXT_IR_GEMINI_PROTOCOL')}")
    print("-" * 60)

    for case in CASES:
        cid = case["id"]
        out_dir = OUT_ROOT / cid
        print(f"\n>>> RUN {cid} mode={case['mode']}")
        print(f"    intent: {case['intent'][:80]}...")
        row: dict = {"id": cid, "mode": case["mode"], "ok": False}
        try:
            rec = enhance(
                case["mode"],
                case["intent"],
                first_frame=case.get("first_frame"),
                duration=case.get("duration"),
                out_dir=out_dir,
                enable_verify=False,
            )
            prompt = rec.get("prompt") or ""
            fails = _check(case, prompt)
            row.update(
                {
                    "ok": not fails,
                    "rounds": len(rec.get("rounds") or []),
                    "duration": rec.get("duration"),
                    "has_d": _has_d_tag(prompt),
                    "camera_keywords": _camera_hint(prompt),
                    "fails": fails,
                    "out_dir": str(out_dir),
                    "preview": prompt[:240].replace("\n", " / "),
                }
            )
            status = "PASS" if row["ok"] else "WARN"
            print(
                f"    {status} rounds={row['rounds']} has_d={row['has_d']} "
                f"camera=[{row['camera_keywords']}]"
            )
            if fails:
                for f in fails:
                    print(f"    - {f}")
            print(f"    preview: {row['preview'][:160]}...")
        except Exception as exc:  # noqa: BLE001
            row["ok"] = False
            row["error"] = str(exc)
            row["traceback"] = traceback.format_exc()
            print(f"    FAIL exception: {exc}")
        summary.append(row)

    summary_path = OUT_ROOT / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    passed = sum(1 for r in summary if r.get("ok"))
    print("\n" + "=" * 60)
    print(f"DONE {passed}/{len(summary)} passed → {summary_path}")
    return 0 if passed == len(summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
