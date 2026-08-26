#!/usr/bin/env python3
"""盲测 pairwise：本地稿 vs 官方 gold_ir，位置互换双跑。

用法：
  python3 scripts/blind_ab.py --pairs input/evalset_v2/blind_pairs.jsonl --out runs/blind_ab
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 jsonl。"""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def pairwise_once(
    judge_chat,
    *,
    intent: str,
    text_a: str,
    text_b: str,
) -> dict[str, Any]:
    """一次 pairwise：不告知来源。"""
    system = (
        "You compare two Context-IR prompts for the same user intent. "
        "Do not assume which is official. Prefer fidelity to intent, then enrichment. "
        'Return JSON {"winner":"A"|"B"|"tie","reason":"...","dims":{}} only.'
    )
    user = (
        f"INTENT:\n{intent}\n\n"
        f"PROMPT_A:\n{text_a[:5000]}\n\n"
        f"PROMPT_B:\n{text_b[:5000]}\n"
    )
    raw = judge_chat(system, user)
    try:
        payload = json.loads(raw.strip())
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        payload = json.loads(raw[start : end + 1]) if start >= 0 and end > start else {"winner": "tie", "reason": "parse_fail"}
    return payload if isinstance(payload, dict) else {"winner": "tie", "reason": "bad_payload"}


def score_pair(
    judge_chat,
    *,
    intent: str,
    local: str,
    official: str,
    rng: random.Random,
) -> dict[str, Any]:
    """双跑：A/B 互换，一致才计胜，否则 tie。"""
    # round1: A=local B=official 或反过来
    swap = rng.random() < 0.5
    a1, b1 = (official, local) if swap else (local, official)
    r1 = pairwise_once(judge_chat, intent=intent, text_a=a1, text_b=b1)
    # round2: 强制互换
    r2 = pairwise_once(judge_chat, intent=intent, text_a=b1, text_b=a1)

    def _map(winner: str, swapped: bool) -> str:
        if winner == "tie":
            return "tie"
        if winner == "A":
            return "official" if swapped else "local"
        if winner == "B":
            return "local" if swapped else "official"
        return "tie"

    w1 = _map(str(r1.get("winner") or "tie"), swap)
    # round2 inputs were swapped relative to round1
    w2 = _map(str(r2.get("winner") or "tie"), (not swap))
    final = w1 if w1 == w2 else "tie"
    return {"winner": final, "round1": r1, "round2": r2, "mapped": [w1, w2]}


def compute_w(results: list[dict[str, Any]]) -> float:
    """W = (win + 0.5*tie) / N。"""
    if not results:
        return 0.0
    win = sum(1 for r in results if r.get("winner") == "local")
    tie = sum(1 for r in results if r.get("winner") == "tie")
    return (win + 0.5 * tie) / len(results)


def main() -> int:
    """入口。"""
    parser = argparse.ArgumentParser(description="Blind A/B vs official Context-IR")
    parser.add_argument("--pairs", type=Path, required=True, help="jsonl: id,intent,local_path,official_path")
    parser.add_argument("--out", type=Path, default=ROOT / "runs" / "blind_ab")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true", help="只检查文件存在，不调用裁判")
    args = parser.parse_args()

    pairs = _load_jsonl(args.pairs)
    rng = random.Random(args.seed)
    results: list[dict[str, Any]] = []

    if args.dry_run:
        for row in pairs:
            local_ok = Path(row["local_path"]).is_file()
            off_ok = Path(row["official_path"]).is_file()
            results.append({"id": row.get("id"), "local_ok": local_ok, "official_ok": off_ok})
        args.out.mkdir(parents=True, exist_ok=True)
        payload = {"dry_run": True, "n": len(results), "results": results}
        (args.out / "blind_ab.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if all(r["local_ok"] and r["official_ok"] for r in results) else 1

    from src.judge import chat_judge  # 若无此符号则用 requests 封装

    def judge_chat(system: str, user: str) -> str:
        """调用本地裁判。"""
        if hasattr(sys.modules.get("src.judge"), "chat_judge"):
            from src import judge as J

            return J.chat_judge(system, user)
        # 回退：复用 judge 内部 HTTP
        from src.judge import _chat  # type: ignore

        return _chat(system, user)

    for row in pairs:
        local = Path(row["local_path"]).read_text(encoding="utf-8")
        official = Path(row["official_path"]).read_text(encoding="utf-8")
        scored = score_pair(
            judge_chat,
            intent=row["intent"],
            local=local,
            official=official,
            rng=rng,
        )
        scored["id"] = row.get("id")
        results.append(scored)

    w = compute_w(results)
    args.out.mkdir(parents=True, exist_ok=True)
    payload = {"W": w, "n": len(results), "results": results}
    (args.out / "blind_ab.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "blind_ab.md").write_text(f"# Blind AB\n\n- W: **{w:.4f}**\n- n: {len(results)}\n", encoding="utf-8")
    print(json.dumps({"W": w, "n": len(results)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
