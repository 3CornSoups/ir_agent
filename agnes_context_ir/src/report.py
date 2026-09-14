"""把“官方管线 vs 本地优化 agent”结果写成报告文件。"""

from __future__ import annotations

import json
import difflib
from pathlib import Path
from typing import Any


def _unified_diff(a: str, b: str) -> str:
    """生成统一 diff，方便直接查看差异。"""
    a_lines = (a or "").splitlines(keepends=True)
    b_lines = (b or "").splitlines(keepends=True)
    diff = difflib.unified_diff(
        a_lines,
        b_lines,
        fromfile="official_prompt(raw)",
        tofile="local_prompt(cleaned)",
    )
    return "".join(diff)


def write_report(
    out_dir: str | Path,
    *,
    record: dict[str, Any],
    prompt_official: str,
    prompt_local: str,
    video_official: dict[str, Any] | None = None,
    video_local: dict[str, Any] | None = None,
) -> None:
    """在 out_dir 写入 report.json / report.md / diff 文件。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    diff_text = _unified_diff(prompt_official, prompt_local)
    report_json: dict[str, Any] = {
        "meta": {
            "mode": record.get("mode"),
            "intent": record.get("intent"),
            "duration": record.get("duration"),
        },
        "prompts": {
            "official": prompt_official,
            "local": prompt_local,
        },
        "verify": record.get("verify") or {},
        "video": {
            "official": video_official,
            "local": video_local,
        },
        "diff": {
            "unified_diff": diff_text,
        },
    }

    (out_dir / "report.json").write_text(
        json.dumps(report_json, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "prompt_diff.txt").write_text(diff_text, encoding="utf-8")

    # report.md 保持纯文本结构，避免把 diff 解释成代码块高亮导致可读性差。
    md = []
    md.append("## Prompt 对比")
    md.append("")
    md.append("### official_prompt(raw)")
    md.append("```")
    md.append(prompt_official.strip())
    md.append("```")
    md.append("")
    md.append("### local_prompt(cleaned)")
    md.append("```")
    md.append(prompt_local.strip())
    md.append("```")
    md.append("")

    verify = record.get("verify") or {}
    if verify:
        md.append("## 质量校验")
        md.append("")
        md.append(f"status: {verify.get('status')} (errors={verify.get('errors')}, warnings={verify.get('warnings')}, fixed={verify.get('fixed')})")
        md.append("")
        for issue in verify.get("issues") or []:
            md.append(f"- [{issue.get('severity')}] {issue.get('code')}: {issue.get('message')}")
        md.append("")

    md.append("### Unified Diff")
    md.append("```")
    md.append(diff_text.rstrip())
    md.append("```")

    if video_official is not None or video_local is not None:
        md.append("")
        md.append("## Video 对比（可选）")
        md.append("")
        md.append("official:")
        md.append(json.dumps(video_official or {}, ensure_ascii=False, indent=2))
        md.append("")
        md.append("local:")
        md.append(json.dumps(video_local or {}, ensure_ascii=False, indent=2))

    (out_dir / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

