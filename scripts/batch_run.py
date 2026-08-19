#!/usr/bin/env python3
"""批量运行 ir_agent 并自动检查结果、更新报告、提交到仓库。

用法示例：
    # 运行内置测试用例集（不出片，只生成提示词）
    python scripts/batch_run.py

    # 运行内置用例 + 出片
    python scripts/batch_run.py --video

    # 运行自定义意图列表文件（每行一个意图，# 开头为注释）
    python scripts/batch_run.py --intents-file my_intents.txt -m t2va

    # 运行后自动 git commit + push
    python scripts/batch_run.py --commit --push

    # 运行后只 commit 不 push
    python scripts/batch_run.py --commit

    # 静默模式（只显示摘要）
    python scripts/batch_run.py --quiet
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 确保 h3.yaml 存在（仅占位；不出片时只读 default_duration）
_H3_YAML = ROOT / "configs" / "h3.yaml"
_H3_EXAMPLE = ROOT / "configs" / "h3.yaml.example"
if not _H3_YAML.exists() and _H3_EXAMPLE.exists():
    import shutil
    shutil.copy(_H3_EXAMPLE, _H3_YAML)
    print(f"[提示] 已从 h3.yaml.example 创建占位 h3.yaml（如需出片请填入真实 API Key）")

from src.pipeline import run_job  # noqa: E402
from src.skill import ALL_MODES  # noqa: E402

# ──────────────────────────────────────────────
# 内置测试用例（覆盖五种模式，均不需要本地图片文件）
# ──────────────────────────────────────────────
BUILTIN_CASES: list[dict[str, Any]] = [
    {
        "id": "t2va_cat",
        "mode": "t2va",
        "intent": "一只橘猫坐在阳光照耀的窗台上，懒洋洋地打了个哈欠，窗外樱花飘落",
    },
    {
        "id": "t2va_city_rain",
        "mode": "t2va",
        "intent": "城市夜雨，霓虹倒映在积水路面，一个撑伞的女孩穿过人行横道",
    },
    {
        "id": "t2va_ocean",
        "mode": "t2va",
        "intent": "无人机俯瞰太平洋日落，橙红色的光芒从地平线铺满海面，远处有一艘孤帆",
    },
    {
        "id": "t2va_dance",
        "mode": "t2va",
        "intent": "霓虹灯舞台上，一名街舞少女随着强劲节拍做 Bboy 旋转，观众挥舞荧光棒",
        "skills": ["street-dance"],
        "skill_router": "off",
    },
    {
        "id": "t2va_forest",
        "mode": "t2va",
        "intent": "深秋森林，阳光穿过金黄落叶，一只梅花鹿缓步走向溪边饮水，远处有雾气",
        "mechanisms": ["因果节拍"],
        "mechanism_router": "off",
    },
]

# ──────────────────────────────────────────────
# 结果数据结构
# ──────────────────────────────────────────────


@dataclass
class CaseResult:
    """单个用例的运行结果。"""

    case_id: str
    mode: str
    intent: str
    ok: bool
    elapsed_sec: float
    out_dir: Path | None = None
    prompt_text: str = ""
    verify_status: str = ""
    verify_errors: int = 0
    verify_warnings: int = 0
    verify_fixed: bool = False
    error_msg: str = ""
    style_skills: list[str] = field(default_factory=list)
    mechanisms: list[str] = field(default_factory=list)


@dataclass
class BatchReport:
    """批量运行汇总报告。"""

    run_at: str
    total: int
    passed: int
    failed: int
    results: list[CaseResult]
    total_elapsed_sec: float


# ──────────────────────────────────────────────
# 核心：运行单个用例
# ──────────────────────────────────────────────


def run_case(
    case: dict[str, Any],
    *,
    make_video: bool = False,
    no_verify: bool = False,
    verbose: bool = True,
) -> CaseResult:
    """运行单条用例，返回结构化结果。"""
    case_id = case["id"]
    mode = case["mode"]
    intent = case["intent"]
    skills = case.get("skills") or None
    skill_router = case.get("skill_router", "hybrid")
    mechanisms = case.get("mechanisms") or None
    mechanism_router = case.get("mechanism_router", "hybrid")

    if verbose:
        print(f"\n[{case_id}] 模式={mode}  意图=「{intent[:40]}{'…' if len(intent) > 40 else ''}」")

    t0 = time.monotonic()
    try:
        rec = run_job(
            mode,
            intent,
            skills=skills,
            skill_router=skill_router,
            mechanisms=mechanisms,
            mechanism_router=mechanism_router,
            make_video=make_video,
            wait_video=False,
            enable_verify=not no_verify,
        )
        elapsed = time.monotonic() - t0

        out_dir = Path(rec["out_dir"])
        prompt_file = out_dir / "prompt.txt"
        prompt_text = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else ""

        verify = rec.get("verify") or {}
        result = CaseResult(
            case_id=case_id,
            mode=mode,
            intent=intent,
            ok=True,
            elapsed_sec=round(elapsed, 1),
            out_dir=out_dir,
            prompt_text=prompt_text,
            verify_status=verify.get("status", ""),
            verify_errors=verify.get("errors", 0),
            verify_warnings=verify.get("warnings", 0),
            verify_fixed=bool(verify.get("fixed")),
            style_skills=rec.get("style_skills") or [],
            mechanisms=rec.get("mechanisms") or [],
        )

        if verbose:
            _print_case_summary(result)
        return result

    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - t0
        result = CaseResult(
            case_id=case_id,
            mode=mode,
            intent=intent,
            ok=False,
            elapsed_sec=round(elapsed, 1),
            error_msg=str(exc),
        )
        if verbose:
            print(f"  [NG] 失败：{exc}")
        return result


def _print_case_summary(r: CaseResult) -> None:
    """打印单个用例的摘要行。"""
    status = "OK" if r.ok else "NG"
    verify_info = ""
    if r.verify_status:
        fixed_tag = "（已修复）" if r.verify_fixed else ""
        verify_info = (
            f" | 校验={r.verify_status}{fixed_tag}"
            f" err={r.verify_errors} warn={r.verify_warnings}"
        )
    skill_info = f" | skills={','.join(r.style_skills)}" if r.style_skills else ""
    mech_info = f" | mechanisms={','.join(r.mechanisms)}" if r.mechanisms else ""
    print(
        f"  [{status}] {r.elapsed_sec:.1f}s"
        f"{verify_info}{skill_info}{mech_info}"
    )
    if r.prompt_text:
        # 打印提示词前 3 行预览
        preview_lines = r.prompt_text.strip().splitlines()[:3]
        for line in preview_lines:
            print(f"    {line[:100]}")


# ──────────────────────────────────────────────
# 检查：判断每个用例是否合格
# ──────────────────────────────────────────────


def check_result(r: CaseResult) -> list[str]:
    """返回该用例的问题列表（空列表表示通过）。"""
    issues: list[str] = []
    if not r.ok:
        issues.append(f"运行异常：{r.error_msg}")
        return issues

    if not r.prompt_text.strip():
        issues.append("prompt.txt 为空")

    if r.verify_status == "error" and not r.verify_fixed:
        issues.append(f"质量校验 error（{r.verify_errors} 个），且修复失败")

    # 检查提示词最低长度（官方示例通常 200+ tokens，这里用字符数做粗估）
    if len(r.prompt_text.strip()) < 200:
        issues.append(f"提示词过短（{len(r.prompt_text.strip())} 字符，期望 ≥200）")

    return issues


# ──────────────────────────────────────────────
# 报告：写入 runs/batch_report.json 与 Markdown
# ──────────────────────────────────────────────

REPORT_JSON = ROOT / "runs" / "batch_report.json"
REPORT_MD = ROOT / "runs" / "batch_report.md"


def write_batch_report(report: BatchReport) -> None:
    """将批量结果写成 JSON 和 Markdown 两份报告。"""
    report_dir = ROOT / "runs"
    report_dir.mkdir(parents=True, exist_ok=True)

    # JSON
    data = {
        "run_at": report.run_at,
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "total_elapsed_sec": round(report.total_elapsed_sec, 1),
        "results": [
            {
                "id": r.case_id,
                "mode": r.mode,
                "intent": r.intent,
                "ok": r.ok,
                "elapsed_sec": r.elapsed_sec,
                "out_dir": str(r.out_dir) if r.out_dir else None,
                "verify_status": r.verify_status,
                "verify_errors": r.verify_errors,
                "verify_warnings": r.verify_warnings,
                "verify_fixed": r.verify_fixed,
                "style_skills": r.style_skills,
                "mechanisms": r.mechanisms,
                "error_msg": r.error_msg,
                "issues": check_result(r),
            }
            for r in report.results
        ],
    }
    REPORT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Markdown
    lines = [
        "# 批量运行报告",
        "",
        f"**运行时间**：{report.run_at}",
        f"**总用例**：{report.total}　**通过**：{report.passed}　**失败**：{report.failed}　"
        f"**总耗时**：{report.total_elapsed_sec:.1f}s",
        "",
        "## 用例明细",
        "",
        "| ID | 模式 | 耗时 | 校验 | 问题 |",
        "| -- | ---- | ---- | ---- | ---- |",
    ]
    for r in report.results:
        issues = check_result(r)
        status_icon = "OK" if r.ok and not issues else "NG"
        verify_cell = r.verify_status if r.verify_status else "—"
        if r.verify_fixed:
            verify_cell += "（已修复）"
        issue_cell = "；".join(issues) if issues else "—"
        lines.append(
            f"| {status_icon} `{r.case_id}` | {r.mode} | {r.elapsed_sec:.1f}s"
            f" | {verify_cell} | {issue_cell} |"
        )

    lines += [
        "",
        "## 提示词预览",
        "",
    ]
    for r in report.results:
        lines.append(f"### {r.case_id}")
        if r.ok and r.prompt_text:
            preview = textwrap.shorten(r.prompt_text.strip(), width=400, placeholder="…")
            lines += [f"```", preview, "```", ""]
        elif r.error_msg:
            lines += [f"> ⚠ {r.error_msg}", ""]
        else:
            lines += ["> （无内容）", ""]

    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写入：{REPORT_JSON}")
    print(f"报告已写入：{REPORT_MD}")


# ──────────────────────────────────────────────
# Git：提交 + 推送
# ──────────────────────────────────────────────


def _git(*args: str, cwd: Path = ROOT) -> tuple[int, str]:
    """运行 git 子命令，返回 (returncode, 合并 stdout+stderr)。"""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode, output


def git_commit_and_push(
    report: BatchReport,
    *,
    do_push: bool = False,
    verbose: bool = True,
) -> bool:
    """将运行报告和输出文件添加到 git 并提交，可选 push。"""

    # 1. 检测是否有可提交内容
    rc, status = _git("status", "--porcelain")
    if not status.strip():
        print("\n[git] 工作区无变更，跳过 commit。")
        return True

    # 2. git add 报告文件 + runs 目录下新增内容
    files_to_add = [
        str(REPORT_JSON.relative_to(ROOT)),
        str(REPORT_MD.relative_to(ROOT)),
        "runs/",
    ]
    rc, out = _git("add", *files_to_add)
    if verbose:
        print(f"\n[git] add → {out or '(ok)'}")

    # 3. 构造 commit 信息
    passed_rate = f"{report.passed}/{report.total}"
    commit_msg = (
        f"chore: 批量运行报告 {report.run_at[:10]} "
        f"({passed_rate} 通过, {report.total_elapsed_sec:.0f}s)"
    )
    rc, out = _git("commit", "-m", commit_msg)
    if verbose:
        print(f"[git] commit → {out}")
    if rc != 0:
        print(f"[git] ⚠ commit 失败（returncode={rc}）")
        return False

    # 4. 可选 push
    if do_push:
        rc, out = _git("push")
        if verbose:
            print(f"[git] push → {out}")
        if rc != 0:
            print(f"[git] ⚠ push 失败（returncode={rc}）")
            return False

    return True


# ──────────────────────────────────────────────
# 加载自定义意图文件
# ──────────────────────────────────────────────


def load_intents_file(path: Path, mode: str) -> list[dict[str, Any]]:
    """从文本文件加载意图列表，每行一个意图，# 开头为注释。"""
    cases = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(
            {
                "id": f"custom_{i:03d}",
                "mode": mode,
                "intent": line,
            }
        )
    return cases


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────


def main() -> int:
    """解析命令行，执行批量运行，生成报告，可选提交。"""
    p = argparse.ArgumentParser(
        description="批量运行 ir_agent，检查结果，生成报告，可选 git commit/push。"
    )
    p.add_argument(
        "--intents-file",
        type=Path,
        help="自定义意图列表文件（每行一条，# 注释），配合 -m 指定模式",
    )
    p.add_argument(
        "-m",
        "--mode",
        choices=ALL_MODES,
        default="t2va",
        help="用于 --intents-file 的生成模式（默认 t2va）",
    )
    p.add_argument(
        "--cases",
        nargs="*",
        help="只运行指定 ID 的内置用例（留空则运行全部内置用例）",
    )
    p.add_argument("--video", action="store_true", help="同时出片（默认只生成提示词）")
    p.add_argument("--no-verify", action="store_true", help="关闭质量校验")
    p.add_argument("--commit", action="store_true", help="运行后 git commit 报告文件")
    p.add_argument("--push", action="store_true", help="git commit 后 push（隐含 --commit）")
    p.add_argument("--quiet", "-q", action="store_true", help="只显示摘要，不打印每条用例详情")
    args = p.parse_args()

    verbose = not args.quiet
    do_commit = args.commit or args.push

    # 选择用例
    if args.intents_file:
        cases = load_intents_file(args.intents_file, args.mode)
        if not cases:
            print(f"[错误] 文件 {args.intents_file} 中没有有效意图行。")
            return 1
        print(f"从文件加载 {len(cases)} 条自定义用例（mode={args.mode}）")
    else:
        cases = BUILTIN_CASES
        if args.cases:
            cases = [c for c in cases if c["id"] in args.cases]
            if not cases:
                print(f"[错误] 未找到指定 ID：{args.cases}")
                return 1

    print(f"将运行 {len(cases)} 个用例  make_video={args.video}  no_verify={args.no_verify}")

    # 批量运行
    results: list[CaseResult] = []
    t_batch_start = time.monotonic()
    for case in cases:
        r = run_case(
            case,
            make_video=args.video,
            no_verify=args.no_verify,
            verbose=verbose,
        )
        results.append(r)

    total_elapsed = time.monotonic() - t_batch_start

    # 汇总
    passed = sum(1 for r in results if r.ok and not check_result(r))
    failed = len(results) - passed
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    report = BatchReport(
        run_at=run_at,
        total=len(results),
        passed=passed,
        failed=failed,
        results=results,
        total_elapsed_sec=total_elapsed,
    )

    # 打印总结
    print("\n" + "=" * 60)
    print(f"批量运行完成：{passed}/{len(results)} 通过  总耗时 {total_elapsed:.1f}s")
    if failed:
        print("失败用例：")
        for r in results:
            issues = check_result(r)
            if issues:
                print(f"  [NG] {r.case_id}：{'；'.join(issues)}")
    print("=" * 60)

    # 写入报告
    write_batch_report(report)

    # Git 操作
    if do_commit:
        git_commit_and_push(report, do_push=args.push, verbose=verbose)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
