#!/usr/bin/env python3
"""从 T8 Creative DNA 库同步机制目录与扩写 overlay（仅写法，不含 H3 字段）。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
T8_DIR = ROOT / "skills" / "t8"
OVERLAYS_DIR = T8_DIR / "overlays"
DEFAULT_REPO = "T8mars/minimax-h3-prompt-skill-T8"
DEFAULT_TAG = "v1.1.8"
ANCHOR_HEADING = "## 必须保留的结构锚点"
SUMMARY_HEADING = "## 推荐输入格式"
PURPOSE_HEADING = "## 用途"


def _raw_url(repo: str, ref: str, path: str) -> str:
    """构造 GitHub raw 内容 URL。"""
    return f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"


def _fetch_text(url: str, *, timeout: int = 60) -> str:
    """下载文本；失败时抛出带 URL 的异常。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"下载失败: {url}") from exc


def _section_lines(text: str, heading: str) -> list[str]:
    """提取 Markdown 二级标题下的非空行。"""
    if heading not in text:
        return []
    chunk = text.split(heading, 1)[1]
    if "## " in chunk:
        chunk = chunk.split("\n## ", 1)[0]
    lines: list[str] = []
    for line in chunk.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-"):
            lines.append(stripped.lstrip("- ").strip())
        elif stripped.startswith(">"):
            lines.append(stripped.lstrip("> ").strip())
        else:
            lines.append(stripped)
    return lines


def _slug_triggers(slug: str, title: str, tags: list[str]) -> list[str]:
    """从中文标题与语义 tags 生成路由触发词（不用 slug 拆词，避免 lockup/action 误命中）。"""
    stop = {"and", "with", "through", "into", "from", "the", "one", "two"}
    out: list[str] = []
    if title:
        out.append(title.strip())
        for piece in re.split(r"[｜|/]", title):
            piece = piece.strip()
            if len(piece) >= 2:
                out.append(piece)
    for tag in tags:
        tag = str(tag).strip()
        if not tag or tag.startswith("t8c"):
            continue
        if tag.lower() in stop:
            continue
        if len(tag) >= 3:
            out.append(tag)
    dedup: list[str] = []
    seen: set[str] = set()
    for item in out:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)
    return dedup[:16]


def _build_overlay(title: str, summary: str, pattern: str, anchors: list[str]) -> str:
    """把 T8 摘要压成扩写/补细节 overlay。"""
    lines = [
        "T8 Creative DNA mechanism — scene-writing overlay only.",
        "",
        "Do not output MiniMax field names or final H3 prompts.",
        "Do not mention aspect ratio, resolution, fps, or canvas size.",
        "Do not reskin a reference case; preserve the user's subject and intent.",
        "Apply anchors as causal beat order, not as copied props or dialogue.",
        "",
        f"Mechanism: {title}",
        f"Summary: {summary.strip()}",
    ]
    if pattern:
        lines.extend(["", f"Recommended beat pattern: {pattern.strip()}"])
    if anchors:
        lines.extend(["", "Mandatory structural anchors (keep order and causality):"])
        lines.extend(f"- {a}" for a in anchors)
    lines.append("")
    return "\n".join(lines)


def _collect_mechanisms(manifest: dict) -> dict[str, dict]:
    """按 slug 去重，合并 tags 与代表 title/summary。"""
    buckets: dict[str, dict] = {}
    for case in manifest.get("cases") or []:
        slug = str(case.get("slug") or "").strip()
        if not slug:
            continue
        tags = [str(t).strip() for t in (case.get("tags") or []) if str(t).strip()]
        entry = buckets.setdefault(
            slug,
            {
                "id": slug,
                "title": str(case.get("title") or slug).strip(),
                "summary": str(case.get("summary") or "").strip(),
                "tags": [],
            },
        )
        if not entry["summary"] and case.get("summary"):
            entry["summary"] = str(case["summary"]).strip()
        for tag in tags:
            if tag not in entry["tags"]:
                entry["tags"].append(tag)
    return buckets


def sync_mechanisms(
    *,
    repo: str = DEFAULT_REPO,
    ref: str = DEFAULT_TAG,
    dry_run: bool = False,
) -> dict[str, int]:
    """拉取 T8 manifest 与各机制 summary，写入 skills/t8/。"""
    manifest_url = _raw_url(repo, ref, "catalog/manifest.json")
    manifest = json.loads(_fetch_text(manifest_url))
    mechanisms = _collect_mechanisms(manifest)
    catalog_version = str(manifest.get("catalog_version") or ref.lstrip("v"))

    yaml_skills: list[dict] = []
    written = 0
    failed: list[str] = []

    for slug in sorted(mechanisms):
        meta = mechanisms[slug]
        summary_path = f"skills/{slug}/references/summary.md"
        summary_url = _raw_url(repo, ref, summary_path)
        try:
            summary_md = _fetch_text(summary_url)
        except RuntimeError:
            failed.append(slug)
            summary_md = ""

        purpose = _section_lines(summary_md, PURPOSE_HEADING)
        pattern_lines = _section_lines(summary_md, SUMMARY_HEADING)
        anchors = _section_lines(summary_md, ANCHOR_HEADING)
        summary = meta["summary"] or (purpose[0] if purpose else "")
        pattern = pattern_lines[0] if pattern_lines else ""
        overlay = _build_overlay(meta["title"], summary, pattern, anchors)

        overlay_rel = f"overlays/{slug}.txt"
        if not dry_run:
            OVERLAYS_DIR.mkdir(parents=True, exist_ok=True)
            (OVERLAYS_DIR / f"{slug}.txt").write_text(overlay, encoding="utf-8")

        yaml_skills.append(
            {
                "id": slug,
                "title": meta["title"],
                "overlay": overlay_rel,
                "description": summary,
                "triggers": _slug_triggers(slug, meta["title"], meta["tags"]),
            }
        )
        written += 1

    version_text = (
        f"source_repo: https://github.com/{repo}\n"
        f"source_ref: {ref}\n"
        f"catalog_version: {catalog_version}\n"
        f"mechanism_count: {written}\n"
    )
    catalog_yaml = (
        "# T8 Creative DNA 机制目录（sync_t8_mechanisms.py 生成）\n"
        f"# 上游: https://github.com/{repo} @ {ref}\n"
        "max_mechanisms: 2\n\n"
    )
    import yaml

    catalog_body = yaml.safe_dump({"skills": yaml_skills}, allow_unicode=True, sort_keys=False)
    catalog_yaml += catalog_body

    if not dry_run:
        T8_DIR.mkdir(parents=True, exist_ok=True)
        (T8_DIR / "VERSION").write_text(version_text, encoding="utf-8")
        (T8_DIR / "catalog.yaml").write_text(catalog_yaml, encoding="utf-8")

    return {"written": written, "failed": len(failed), "failed_ids": failed}


def main() -> int:
    """CLI：同步 T8 机制到 skills/t8/。"""
    parser = argparse.ArgumentParser(description="同步 T8 Creative DNA 机制目录")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--ref", default=DEFAULT_TAG, help="Git tag 或 commit（默认 v1.1.8）")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    args = parser.parse_args()
    stats = sync_mechanisms(repo=args.repo, ref=args.ref, dry_run=args.dry_run)
    print(
        f"T8 mechanisms: {stats['written']} written, {stats['failed']} fetch failures",
        file=sys.stderr,
    )
    if stats.get("failed_ids"):
        print("failed:", ", ".join(stats["failed_ids"][:10]), file=sys.stderr)
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
