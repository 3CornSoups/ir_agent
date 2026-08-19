#!/usr/bin/env python3
"""
批量生成 MiniMax-H3 视频提示词，并自动检查结果质量、写报告、可选提交仓库。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【同事使用指南】

1. 程序装在哪里？
   服务器路径（示例）：/data/yourname/ir_agent/
   本脚本路径：       /data/yourname/ir_agent/scripts/batch_run.py

2. 我的输入文件放哪里？
   ┌──────────────────────────────────────────────────────┐
   │  纯文字任务：写一个 TXT 文件，每行一条意图描述        │
   │  示例：/data/yourname/ir_agent/input/my_tasks.txt    │
   │                                                      │
   │  带图片/视频的任务：把素材文件放到任意路径，          │
   │  在意图文件里用绝对路径引用（见下方格式说明）         │
   └──────────────────────────────────────────────────────┘

3. 输出文件在哪里？
   ┌──────────────────────────────────────────────────────┐
   │  每次运行会在 runs/ 下自动创建一个带时间戳的子目录：  │
   │  runs/{模式}_{时间戳}/                               │
   │    ├── prompt.txt       ← 最终生成的提示词（主产物）  │
   │    ├── expand.txt       ← 扩写阶段草稿               │
   │    ├── elaborate.txt    ← 补细节阶段草稿              │
   │    └── run.json         ← 本次运行的完整元数据        │
   │                                                      │
   │  批量报告（汇总所有用例）：                           │
   │    runs/batch_report.md   ← Markdown 汇总（推荐查看）│
   │    runs/batch_report.json ← JSON 格式汇总            │
   └──────────────────────────────────────────────────────┘

4. 意图文件格式（--intents-file 参数读取的 TXT 文件）：

   【纯文字（t2va 模式）】每行一条意图，# 开头为注释：
   ─────────────────────────────
   # 这是注释，会被跳过
   一只橘猫坐在窗台上打哈欠，窗外樱花飘落
   城市夜雨，霓虹倒映积水路面，女孩撑伞穿过人行横道
   ─────────────────────────────

   【带首帧图（i2va 模式）】格式：意图|||first=图片绝对路径：
   ─────────────────────────────
   猫咪从窗台跳下|||first=/data/yourname/photos/cat.png
   ─────────────────────────────

   【带首尾帧（fl2va 模式）】格式：意图|||first=图1路径|||last=图2路径：
   ─────────────────────────────
   人物走向远处|||first=/data/yourname/a.png|||last=/data/yourname/b.png
   ─────────────────────────────

   【带参考素材（r2va 模式）】格式：意图|||ref_image=路径（可多个 | 分隔）：
   ─────────────────────────────
   保持人设走路|||ref_image=/data/yourname/face.png|||ref_video=/data/yourname/walk.mp4
   ─────────────────────────────

5. 快速上手（3 条命令）：
   # 第一步：进入项目目录
   cd /data/yourname/ir_agent

   # 第二步：准备意图文件（或直接用内置测试用例）
   echo "一只橘猫在窗台晒太阳" > input/my_tasks.txt

   # 第三步：运行
   python scripts/batch_run.py --intents-file input/my_tasks.txt -m t2va

6. 常用命令示例：
   # 运行内置 5 个测试用例（验证环境是否正常）
   python scripts/batch_run.py

   # 运行自己的意图文件（t2va 文字生视频）
   python scripts/batch_run.py --intents-file input/my_tasks.txt -m t2va

   # 运行自己的意图文件（i2va 图片转视频，文件里含 |||first= 路径）
   python scripts/batch_run.py --intents-file input/my_tasks.txt -m i2va

   # 只运行内置用例里的某几条（用空格分隔 ID）
   python scripts/batch_run.py --cases t2va_cat t2va_dance

   # 不做质量校验（更快，省 1 次 API 调用）
   python scripts/batch_run.py --intents-file input/my_tasks.txt --no-verify

   # 把结果 commit 到 git 仓库（方便对比不同版本效果）
   python scripts/batch_run.py --intents-file input/my_tasks.txt --commit

   # 安静模式（只看最终汇总，不打印每条详情）
   python scripts/batch_run.py --intents-file input/my_tasks.txt -q

   # ────── 官方 Context-IR 管线 ──────

   # 本地 + 官方同时跑，生成对照（需要 MiniMax API Key）
   python scripts/batch_run.py --intents-file input/my_tasks.txt --official --official-key YOUR_KEY

   # 或者用环境变量传 Key
   export MINIMAX_API_KEY=YOUR_KEY
   python scripts/batch_run.py --intents-file input/my_tasks.txt --official

   # 只走官方管线，不跑本地（纯测试官方效果）
   python scripts/batch_run.py --intents-file input/my_tasks.txt --official-only --official-key YOUR_KEY

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 项目根目录（本脚本在 scripts/ 下，向上一级就是根目录）
ROOT = Path(__file__).resolve().parent.parent

# 把项目根目录加入 Python 搜索路径，确保 `from src.xxx` 能找到
sys.path.insert(0, str(ROOT))

# ── 首次运行时自动创建 h3.yaml 占位文件 ──────────────────────
# h3.yaml 是 MiniMax H3 出片 API 的配置文件。
# 只生成提示词（不出片）时它仅用于读默认时长，所以用 example 占位就够了。
# 如果你需要真正出片，请把 configs/h3.yaml 里的 api_key 替换为你的真实密钥。
_H3_YAML = ROOT / "configs" / "h3.yaml"
_H3_EXAMPLE = ROOT / "configs" / "h3.yaml.example"
if not _H3_YAML.exists() and _H3_EXAMPLE.exists():
    shutil.copy(_H3_EXAMPLE, _H3_YAML)
    print("[提示] 已从 h3.yaml.example 创建占位 h3.yaml")
    print("       如需出片，请编辑 configs/h3.yaml 填入真实的 MINIMAX_API_KEY")

from src.pipeline import run_job  # noqa: E402
from src.skill import ALL_MODES  # noqa: E402

# 官方 Context-IR 管线支持（复用 compare_context_ir.py 里的调用逻辑）
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("compare_context_ir", ROOT / "scripts" / "compare_context_ir.py")
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
call_official_context_ir = _mod.call_official_context_ir

# ──────────────────────────────────────────────────────────
# 内置测试用例
# 用途：验证当前环境是否正常（运行 `python scripts/batch_run.py` 即可触发）
# 这些用例都是纯文字 t2va，不需要任何本地图片/视频文件。
# ──────────────────────────────────────────────────────────
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
        "skills": ["street-dance"],       # 强制加载街舞题材写法
        "skill_router": "off",            # 关闭自动路由，直接用上方指定的 skill
    },
    {
        "id": "t2va_forest",
        "mode": "t2va",
        "intent": "深秋森林，阳光穿过金黄落叶，一只梅花鹿缓步走向溪边饮水，远处有雾气",
        "mechanisms": ["因果节拍"],       # 强制加载 T8 Creative DNA 机制
        "mechanism_router": "off",
    },
]


# ──────────────────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────────────────


@dataclass
class CaseResult:
    """单条用例的运行结果（内部使用）。"""

    case_id: str
    mode: str
    intent: str
    ok: bool
    elapsed_sec: float
    source: str = "local"  # "local" = 本地增强管线, "official" = 官方 Context-IR
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
    """批量运行的汇总报告（内部使用）。"""

    run_at: str
    total: int
    passed: int
    failed: int
    results: list[CaseResult]
    total_elapsed_sec: float


# ──────────────────────────────────────────────────────────
# 运行单条用例
# ──────────────────────────────────────────────────────────


def run_case(
    case: dict[str, Any],
    *,
    make_video: bool = False,
    no_verify: bool = False,
    verbose: bool = True,
) -> CaseResult:
    """调用管线运行单条用例，返回结构化结果。"""
    case_id = case["id"]
    mode = case["mode"]
    intent = case["intent"]
    skills = case.get("skills") or None
    skill_router = case.get("skill_router", "hybrid")
    mechanisms = case.get("mechanisms") or None
    mechanism_router = case.get("mechanism_router", "hybrid")
    first_frame = case.get("first_frame")
    last_frame = case.get("last_frame")
    ref_images = case.get("ref_images") or None
    ref_videos = case.get("ref_videos") or None
    ref_audios = case.get("ref_audios") or None

    if verbose:
        preview = intent[:50] + ("..." if len(intent) > 50 else "")
        print(f"\n[{case_id}]  模式={mode}")
        print(f"  意图：{preview}")
        if first_frame:
            print(f"  首帧：{first_frame}")
        if last_frame:
            print(f"  尾帧：{last_frame}")
        if ref_images:
            print(f"  参考图：{ref_images}")
        if ref_videos:
            print(f"  参考视频：{ref_videos}")

    t0 = time.monotonic()
    try:
        rec = run_job(
            mode,
            intent,
            first_frame=first_frame,
            last_frame=last_frame,
            reference_images=ref_images,
            reference_videos=ref_videos,
            reference_audios=ref_audios,
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


def run_case_official(
    case: dict[str, Any],
    *,
    official_cfg: dict[str, Any],
    verbose: bool = True,
) -> CaseResult:
    """调用官方 MiniMax H3-Context-IR API 生成提示词，返回结构化结果。"""
    case_id = case["id"] + "_official"
    mode = case["mode"]
    intent = case["intent"]
    first_frame = case.get("first_frame")
    last_frame = case.get("last_frame")
    ref_images = case.get("ref_images") or None
    ref_videos = case.get("ref_videos") or None
    ref_audios = case.get("ref_audios") or None

    if verbose:
        preview = intent[:50] + ("..." if len(intent) > 50 else "")
        print(f"\n[{case_id}]  模式={mode}  (官方 Context-IR)")
        print(f"  意图：{preview}")

    t0 = time.monotonic()
    try:
        result = call_official_context_ir(
            official_cfg,
            mode,
            intent,
            duration=official_cfg.get("default_duration", 5),
            first_frame=first_frame,
            last_frame=last_frame,
            reference_images=ref_images,
            reference_videos=ref_videos,
            reference_audios=ref_audios,
        )
        elapsed = time.monotonic() - t0

        prompt_text = result.get("prompt", "")

        # 写入输出目录
        from datetime import datetime as _dt
        stamp = _dt.now().strftime("%Y%m%d_%H%M%S")
        out_dir = ROOT / "runs" / f"{mode}_official_{stamp}"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "prompt.txt").write_text(prompt_text, encoding="utf-8")
        (out_dir / "run.json").write_text(
            json.dumps({"source": "official", "task_id": result.get("task_id", ""), "mode": mode, "intent": intent},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        cr = CaseResult(
            case_id=case_id,
            mode=mode,
            intent=intent,
            ok=True,
            elapsed_sec=round(elapsed, 1),
            source="official",
            out_dir=out_dir,
            prompt_text=prompt_text,
        )
        if verbose:
            _print_case_summary(cr)
        return cr

    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - t0
        cr = CaseResult(
            case_id=case_id,
            mode=mode,
            intent=intent,
            ok=False,
            elapsed_sec=round(elapsed, 1),
            source="official",
            error_msg=str(exc),
        )
        if verbose:
            print(f"  [NG] 官方管线失败：{exc}")
        return cr


def _print_case_summary(r: CaseResult) -> None:
    """打印单条用例运行完毕后的摘要行。"""
    status = "OK" if r.ok else "NG"
    verify_info = ""
    if r.verify_status:
        fixed_tag = "（已自动修复）" if r.verify_fixed else ""
        verify_info = (
            f"  质量校验={r.verify_status}{fixed_tag}"
            f" (error={r.verify_errors}, warning={r.verify_warnings})"
        )
    skill_info = f"  skills={','.join(r.style_skills)}" if r.style_skills else ""
    mech_info = f"  mechanisms={','.join(r.mechanisms)}" if r.mechanisms else ""

    source_tag = "[本地]" if r.source == "local" else "[官方]"
    print(f"  [{status}] {source_tag} 耗时={r.elapsed_sec:.1f}s{verify_info}{skill_info}{mech_info}")

    if r.out_dir:
        print(f"  输出目录：{r.out_dir}")
        print(f"  提示词文件：{r.out_dir / 'prompt.txt'}")

    if r.prompt_text:
        # 打印提示词前 4 行，帮助快速判断内容是否合理
        lines = r.prompt_text.strip().splitlines()[:4]
        print("  提示词预览（前4行）：")
        for line in lines:
            print(f"    {line[:110]}")


# ──────────────────────────────────────────────────────────
# 质量检查（在已有的提示词质量校验之上，额外做简单完整性检查）
# ──────────────────────────────────────────────────────────


def check_result(r: CaseResult) -> list[str]:
    """对单条结果做完整性检查，返回问题列表（空列表表示通过）。"""
    issues: list[str] = []
    if not r.ok:
        issues.append(f"运行异常：{r.error_msg}")
        return issues

    if not r.prompt_text.strip():
        issues.append("prompt.txt 为空，未生成任何内容")

    if r.verify_status == "error" and not r.verify_fixed:
        issues.append(
            f"提示词质量校验存在 {r.verify_errors} 个 error，且自动修复失败，"
            "建议人工检查 prompt.txt"
        )

    # 字符数粗估（官方输出通常 500+ 字符，低于 200 基本说明内容不完整）
    char_count = len(r.prompt_text.strip())
    if char_count < 200:
        issues.append(f"提示词内容过短（{char_count} 字符，期望 ≥200）")

    return issues


# ──────────────────────────────────────────────────────────
# 写报告
# 报告位置：
#   runs/batch_report.md   （Markdown，推荐直接用浏览器/VSCode 查看）
#   runs/batch_report.json （JSON，方便程序读取）
# ──────────────────────────────────────────────────────────

REPORT_JSON = ROOT / "runs" / "batch_report.json"
REPORT_MD = ROOT / "runs" / "batch_report.md"


def write_batch_report(report: BatchReport) -> None:
    """将批量结果写成 JSON 和 Markdown 两份报告文件。"""
    report_dir = ROOT / "runs"
    report_dir.mkdir(parents=True, exist_ok=True)

    # ── JSON ──
    data = {
        "run_at": report.run_at,
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "total_elapsed_sec": round(report.total_elapsed_sec, 1),
        "results": [
            {
                "id": r.case_id,
                "source": r.source,
                "mode": r.mode,
                "intent": r.intent,
                "ok": r.ok,
                "elapsed_sec": r.elapsed_sec,
                "out_dir": str(r.out_dir) if r.out_dir else None,
                "prompt_file": str(r.out_dir / "prompt.txt") if r.out_dir else None,
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

    # ── Markdown ──
    lines = [
        "# ir_agent 批量运行报告",
        "",
        f"**运行时间**：{report.run_at}",
        (
            f"**总用例**：{report.total}　"
            f"**通过**：{report.passed}　"
            f"**失败**：{report.failed}　"
            f"**总耗时**：{report.total_elapsed_sec:.1f}s"
        ),
        "",
        "---",
        "",
        "## 用例明细",
        "",
        "| 状态 | ID | 来源 | 模式 | 耗时 | 质量校验 | 输出目录 | 问题 |",
        "| ---- | -- | ---- | ---- | ---- | -------- | -------- | ---- |",
    ]
    for r in report.results:
        issues = check_result(r)
        status_icon = "OK" if r.ok and not issues else "NG"
        source_cell = "本地" if r.source == "local" else "官方"
        verify_cell = r.verify_status if r.verify_status else "—"
        if r.verify_fixed:
            verify_cell += "(已修复)"
        issue_cell = "；".join(issues) if issues else "—"
        out_cell = f"`{r.out_dir.name}`" if r.out_dir else "—"
        lines.append(
            f"| {status_icon} | `{r.case_id}` | {source_cell} | {r.mode} | {r.elapsed_sec:.1f}s"
            f" | {verify_cell} | {out_cell} | {issue_cell} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 提示词预览",
        "",
        "> 以下为各用例生成的 `prompt.txt` 前 400 字预览，完整内容请查看对应的输出目录。",
        "",
    ]
    for r in report.results:
        issues = check_result(r)
        status_icon = "OK" if r.ok and not issues else "NG"
        out_path = str(r.out_dir / "prompt.txt") if r.out_dir else "—"
        lines.append(f"### [{status_icon}] {r.case_id}")
        lines.append(f"- **模式**：{r.mode}")
        lines.append(f"- **意图**：{r.intent}")
        lines.append(f"- **提示词文件**：`{out_path}`")
        if r.ok and r.prompt_text:
            preview = textwrap.shorten(r.prompt_text.strip(), width=400, placeholder="...")
            lines += ["", "```", preview, "```", ""]
        elif r.error_msg:
            lines += ["", f"> 运行失败：{r.error_msg}", ""]
        else:
            lines += ["", "> （无内容）", ""]

    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n批量报告（Markdown）：{REPORT_MD}")
    print(f"批量报告（JSON）    ：{REPORT_JSON}")


# ──────────────────────────────────────────────────────────
# Git 提交（可选功能）
# ──────────────────────────────────────────────────────────


def _git(*args: str, cwd: Path = ROOT) -> tuple[int, str]:
    """执行 git 子命令，返回 (exit_code, stdout+stderr 合并文本)。"""
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
    """将报告文件加入 git 并提交，可选 push 到远程。"""
    rc, status = _git("status", "--porcelain")
    if not status.strip():
        print("\n[git] 工作区无变更，跳过 commit。")
        return True

    files_to_add = [
        str(REPORT_JSON.relative_to(ROOT)),
        str(REPORT_MD.relative_to(ROOT)),
        "runs/",
    ]
    rc, out = _git("add", *files_to_add)
    if verbose:
        print(f"\n[git] add  → {out or '(ok)'}")

    passed_rate = f"{report.passed}/{report.total}"
    commit_msg = (
        f"chore: batch_run {report.run_at[:10]} "
        f"({passed_rate} passed, {report.total_elapsed_sec:.0f}s)"
    )
    rc, out = _git("commit", "-m", commit_msg)
    if verbose:
        print(f"[git] commit → {out}")
    if rc != 0:
        print(f"[git] commit 失败（code={rc}）")
        return False

    if do_push:
        rc, out = _git("push")
        if verbose:
            print(f"[git] push → {out}")
        if rc != 0:
            print(f"[git] push 失败（code={rc}）")
            return False

    return True


# ──────────────────────────────────────────────────────────
# 加载意图文件
# ──────────────────────────────────────────────────────────

_FIELD_SEP = "|||"  # 意图文件中字段的分隔符


def load_intents_file(path: Path, mode: str) -> list[dict[str, Any]]:
    """
    从文本文件加载意图列表。

    文件格式（每行一条任务，# 开头为注释）：

    纯文字（t2va）：
        一只橘猫在窗台晒太阳

    带首帧（i2va）：
        猫咪从窗台跳下|||first=/data/yourname/cat.png

    带首尾帧（fl2va）：
        人物走向远处|||first=/data/yourname/a.png|||last=/data/yourname/b.png

    带参考素材（r2va）：
        保持人设走路|||ref_image=/data/yourname/face.png|||ref_video=/data/yourname/walk.mp4

    多个同类字段用相同 key 重复出现即可（如两张参考图）：
        保持风格|||ref_image=/data/face1.png|||ref_image=/data/face2.png
    """
    cases = []
    raw_lines = path.read_text(encoding="utf-8").splitlines()

    for lineno, raw in enumerate(raw_lines, 1):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue

        # 用 ||| 分割字段
        parts = [p.strip() for p in raw.split(_FIELD_SEP)]
        intent = parts[0]
        if not intent:
            print(f"[警告] 第 {lineno} 行意图为空，已跳过：{raw!r}")
            continue

        case: dict[str, Any] = {
            "id": f"line_{lineno:04d}",
            "mode": mode,
            "intent": intent,
        }

        ref_images: list[str] = []
        ref_videos: list[str] = []
        ref_audios: list[str] = []

        for field_str in parts[1:]:
            if "=" not in field_str:
                print(f"[警告] 第 {lineno} 行字段格式错误（缺少 =），已忽略：{field_str!r}")
                continue
            key, _, val = field_str.partition("=")
            key = key.strip().lower()
            val = val.strip()

            if key == "first":
                _check_file_exists(val, lineno, "首帧")
                case["first_frame"] = val
            elif key == "last":
                _check_file_exists(val, lineno, "尾帧")
                case["last_frame"] = val
            elif key == "ref_image":
                _check_file_exists(val, lineno, "参考图")
                ref_images.append(val)
            elif key == "ref_video":
                _check_file_exists(val, lineno, "参考视频")
                ref_videos.append(val)
            elif key == "ref_audio":
                _check_file_exists(val, lineno, "参考音频")
                ref_audios.append(val)
            else:
                print(f"[警告] 第 {lineno} 行未知字段 key={key!r}，已忽略。")

        if ref_images:
            case["ref_images"] = ref_images
        if ref_videos:
            case["ref_videos"] = ref_videos
        if ref_audios:
            case["ref_audios"] = ref_audios

        cases.append(case)

    return cases


def _check_file_exists(path_str: str, lineno: int, label: str) -> None:
    """检查用户指定的素材文件是否存在，不存在时打印警告（不中断）。"""
    p = Path(path_str)
    if not p.exists():
        print(
            f"[警告] 第 {lineno} 行 {label} 文件不存在：{path_str}\n"
            "       请检查路径是否正确，运行时可能报错。"
        )


# ──────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────


def main() -> int:
    """解析命令行参数，执行批量运行，写报告，可选提交。"""
    p = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "批量生成 MiniMax-H3 视频提示词，检查质量，写报告，可选 git commit/push。\n"
            "详细使用说明见脚本顶部注释，或查阅 README.md。\n"
            "\n"
            "输出位置：runs/{模式}_{时间戳}/prompt.txt\n"
            "报告位置：runs/batch_report.md  /  runs/batch_report.json"
        ),
    )
    p.add_argument(
        "--intents-file",
        type=Path,
        metavar="PATH",
        help=(
            "意图列表文件路径（TXT，每行一条任务）。\n"
            "支持带素材路径，格式：意图|||first=图片路径|||ref_image=参考图路径\n"
            "（详见脚本顶部说明）"
        ),
    )
    p.add_argument(
        "-m",
        "--mode",
        choices=ALL_MODES,
        default="t2va",
        metavar="MODE",
        help=(
            f"生成模式，仅对 --intents-file 有效（默认 t2va）。\n"
            f"可选：{' / '.join(ALL_MODES)}\n"
            "  t2va  = 纯文字 → 视频提示词\n"
            "  i2va  = 首帧图 + 文字 → 视频提示词\n"
            "  fl2va = 首帧 + 尾帧 + 文字 → 视频提示词\n"
            "  l2va  = 尾帧图 + 文字 → 视频提示词\n"
            "  r2va  = 参考图/视频/音频 + 文字 → 视频提示词"
        ),
    )
    p.add_argument(
        "--cases",
        nargs="*",
        metavar="ID",
        help=(
            "只运行指定 ID 的内置测试用例（用空格分隔）。\n"
            f"内置用例 ID：{' '.join(c['id'] for c in BUILTIN_CASES)}\n"
            "不指定此参数且不指定 --intents-file 时，运行全部内置用例。"
        ),
    )
    p.add_argument(
        "--official",
        action="store_true",
        help=(
            "同时走官方 MiniMax Context-IR 管线生成提示词。\n"
            "每条意图会同时生成「本地」和「官方」两份提示词，方便对照。\n"
            "需要配合 --official-key 或环境变量 MINIMAX_API_KEY 提供密钥。"
        ),
    )
    p.add_argument(
        "--official-only",
        action="store_true",
        help=(
            "只走官方 Context-IR 管线，不走本地管线。\n"
            "适合想单独测试官方效果时使用。"
        ),
    )
    p.add_argument(
        "--official-key",
        default="",
        metavar="KEY",
        help="官方 MiniMax API Key（也可通过环境变量 MINIMAX_API_KEY 设置）。",
    )
    p.add_argument(
        "--official-base-url",
        default="https://api.minimaxi.com",
        metavar="URL",
        help="官方 API base URL（默认 https://api.minimaxi.com）。",
    )
    p.add_argument(
        "--video",
        action="store_true",
        help="同时调用 H3 出片 API（默认只生成提示词，不出片）。需要配置好 configs/h3.yaml。",
    )
    p.add_argument(
        "--no-verify",
        action="store_true",
        help="跳过提示词质量校验（节省 0~1 次 API 调用，速度更快）。",
    )
    p.add_argument(
        "--commit",
        action="store_true",
        help="运行完成后将报告文件 git commit 到本地仓库。",
    )
    p.add_argument(
        "--push",
        action="store_true",
        help="运行完成后将报告 commit 并 push 到远程仓库（自动隐含 --commit）。",
    )
    p.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="安静模式：只显示每条用例的单行摘要，不打印提示词预览。",
    )
    args = p.parse_args()

    verbose = not args.quiet
    do_commit = args.commit or args.push

    # ── 官方管线配置 ──
    import os
    use_official = args.official or args.official_only
    official_cfg: dict[str, Any] | None = None
    if use_official:
        api_key = args.official_key or os.environ.get("MINIMAX_API_KEY", "")
        if not api_key:
            print("[错误] 使用官方管线需要提供 API Key：")
            print("       方式 1：命令行加 --official-key YOUR_KEY")
            print("       方式 2：设置环境变量 export MINIMAX_API_KEY=YOUR_KEY")
            return 1
        official_cfg = {
            "api_key": api_key,
            "base_url": args.official_base_url.rstrip("/"),
            "model": "MiniMax-H3",
            "timeout_sec": 120,
            "poll_interval_sec": 5,
            "poll_timeout_sec": 1800,
            "default_duration": 5,
        }

    # ── 选择要运行的用例 ──
    if args.intents_file:
        if not args.intents_file.exists():
            print(f"[错误] 意图文件不存在：{args.intents_file}")
            print(f"       请检查路径是否正确。当前工作目录：{Path.cwd()}")
            return 1
        cases = load_intents_file(args.intents_file, args.mode)
        if not cases:
            print(f"[错误] 文件 {args.intents_file} 中没有有效的意图行。")
            return 1
        print(f"从文件加载 {len(cases)} 条用例  模式={args.mode}")
        print(f"文件路径：{args.intents_file.resolve()}")
    else:
        cases = BUILTIN_CASES
        if args.cases:
            cases = [c for c in cases if c["id"] in args.cases]
            if not cases:
                print(f"[错误] 未找到指定的内置用例 ID：{args.cases}")
                print(f"       可用 ID：{[c['id'] for c in BUILTIN_CASES]}")
                return 1

    pipeline_desc = "本地" if not use_official else ("官方" if args.official_only else "本地+官方对照")
    print(
        f"\n将运行 {len(cases)} 个用例"
        f"  管线={pipeline_desc}"
        f"  出片={args.video}"
        f"  质量校验={'关闭' if args.no_verify else '开启'}"
    )
    print(f"输出根目录：{ROOT / 'runs'}\n")

    # ── 批量运行 ──
    results: list[CaseResult] = []
    t_batch_start = time.monotonic()
    for case in cases:
        # 本地管线
        if not args.official_only:
            r = run_case(
                case,
                make_video=args.video,
                no_verify=args.no_verify,
                verbose=verbose,
            )
            results.append(r)

        # 官方管线
        if use_official and official_cfg:
            r_official = run_case_official(
                case,
                official_cfg=official_cfg,
                verbose=verbose,
            )
            results.append(r_official)

    total_elapsed = time.monotonic() - t_batch_start

    # ── 汇总统计 ──
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

    # ── 打印总结 ──
    print("\n" + "=" * 65)
    print(f"  批量运行完成：{passed}/{len(results)} 通过   总耗时 {total_elapsed:.1f}s")
    if failed:
        print(f"  失败用例：")
        for r in results:
            issues = check_result(r)
            if issues:
                print(f"    [NG] {r.case_id}：{'；'.join(issues)}")
    if passed > 0:
        print(f"  提示词输出目录：{ROOT / 'runs'}/")
    print("=" * 65)

    # ── 写报告 ──
    write_batch_report(report)

    # ── Git 操作（可选）──
    if do_commit:
        git_commit_and_push(report, do_push=args.push, verbose=verbose)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
