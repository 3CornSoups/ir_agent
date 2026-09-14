#!/usr/bin/env python3
"""从当前 prompts / skills 重新生成 docs/pipeline-logic-viewer.html。

设计要点：
- 构建期把正文写成静态 DOM（非仅 JSON + JS 注入），便于沉浸式翻译识别
- 可翻译正文用 <p> 段落，不用 <pre>/<code>（插件默认常跳过或逐行翻坏）
- 页面注入 immersiveTranslateConfig，显式指定 .imt-block
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
SKILLS = PROMPTS / "skills"
EXAMPLES = PROMPTS / "examples"
OUT = ROOT / "docs" / "pipeline-logic-viewer.html"


def _read(path: Path) -> str:
    """读取 UTF-8 文本。"""
    return path.read_text(encoding="utf-8")


def _paras(text: str) -> str:
    """把文本拆成可翻译的 <p> 段落（按空行分段）。"""
    chunks = [c.strip() for c in (text or "").replace("\r\n", "\n").split("\n\n")]
    chunks = [c for c in chunks if c]
    if not chunks:
        return '<p class="imt-p">(empty)</p>'
    parts: list[str] = []
    for c in chunks:
        # 段内单换行保留为 <br>，仍属同一翻译块
        body = html.escape(c).replace("\n", "<br>\n")
        parts.append(f'<p class="imt-p" lang="en" translate="yes">{body}</p>')
    return "\n".join(parts)


def _block(title: str, body: str, *, note: str = "") -> str:
    """渲染一块可翻译正文卡片。"""
    note_html = f'<p class="block-note">{html.escape(note)}</p>' if note else ""
    return f"""
<article class="doc-card imt-block" translate="yes">
  <header class="doc-card__head">
    <h3 class="doc-card__title">{html.escape(title)}</h3>
    {note_html}
  </header>
  <div class="doc-card__body imt-body">
    {_paras(body)}
  </div>
</article>
"""


def _chip(label: str) -> str:
    """渲染一枚规则卡标签。"""
    return f'<span class="chip">{html.escape(label)}</span>'


def _collect() -> dict:
    """收集阶段提示词、规则卡、示例与装配后的 SYSTEM。"""
    sys.path.insert(0, str(ROOT))
    from src.config import SKILL_SETS, load_skills  # noqa: WPS433
    from src.prompts import compose_format_system, compose_plan_system

    files = {
        "_core": _read(PROMPTS / "_core.txt"),
        "r1_keyinfo": _read(PROMPTS / "r1_keyinfo.txt"),
        "r1_image_guide": _read(PROMPTS / "r1_image_guide.txt"),
        "r1_perceive_fuse": _read(PROMPTS / "r1_perceive_fuse.txt"),
        "r2_plot": _read(PROMPTS / "r2_plot.txt"),
        "r2_format": _read(PROMPTS / "r2_format.txt"),
        "r3_format_r2va": _read(PROMPTS / "r3_format_r2va.txt"),
    }
    skills = {p.stem: _read(p) for p in sorted(SKILLS.glob("*.txt"))}
    examples = {p.stem: _read(p) for p in sorted(EXAMPLES.glob("*.txt"))}
    composed = {
        "plan_t2va": compose_plan_system("r1_keyinfo", "t2va"),
        "format_t2va": compose_format_system("r2_format", "t2va"),
        "plan_keyframe": compose_plan_system("r1_image_guide", "i2va"),
        "format_keyframe": compose_format_system("r2_format", "i2va"),
        "plan_r2va_r1": compose_plan_system(
            "r1_perceive_fuse", "r2va", stage="r1_perceive_fuse"
        ),
        "plan_r2va_r2": compose_plan_system("r2_plot", "r2va", stage="r2_plot"),
        "format_r2va": compose_format_system("r3_format_r2va", "r2va"),
    }
    skill_set_bodies = {name: load_skills(name) for name in SKILL_SETS}
    return {
        "files": files,
        "skills": skills,
        "examples": examples,
        "skill_sets": {k: list(v) for k, v in SKILL_SETS.items()},
        "skill_set_bodies": skill_set_bodies,
        "composed": composed,
    }


MODES = [
    {
        "id": "t2va",
        "label": "T2VA 纯文",
        "rounds_n": 2,
        "blurb": "短意图 → 规划（CAST / SHOT PLAN）→ 三字段格式化。固定 2 次 Gemini HTTP。",
        "rounds": [
            {
                "http": "第 1 次请求",
                "stage": "r1_keyinfo",
                "role": "规划轮",
                "composed": "plan_t2va",
                "set": "plan_t2va",
                "cn": "抽出约束、说话人名册与分镜计划，写出英文场景散文。",
            },
            {
                "http": "第 2 次请求",
                "stage": "r2_format",
                "role": "格式终轮",
                "composed": "format_t2va",
                "set": "format_t2va",
                "final": True,
                "cn": "序列化为 integrated_multimodal_description / overall_soundscape / non_diegetic_music。",
            },
        ],
    },
    {
        "id": "keyframe",
        "label": "关键帧 I2VA / FL2VA / L2VA",
        "rounds_n": 2,
        "blurb": "看关键帧图 → 规划 → 对齐句 + 三字段。含 s02_alignment 规则卡。",
        "rounds": [
            {
                "http": "第 1 次请求",
                "stage": "r1_image_guide",
                "role": "感知 + 规划",
                "composed": "plan_keyframe",
                "set": "plan_keyframe",
                "cn": "画面事实 + CAST / SHOT PLAN + 展开/桥接/倒推引导。",
            },
            {
                "http": "第 2 次请求",
                "stage": "r2_format",
                "role": "格式终轮",
                "composed": "format_keyframe",
                "set": "format_keyframe",
                "final": True,
                "cn": "先写官方对齐句，再写三字段；禁止终轮新编对白。",
            },
        ],
    },
    {
        "id": "r2va",
        "label": "R2VA 多参考",
        "rounds_n": 3,
        "blurb": "感知融合 → 情节分镜 → 六段式。含 retention / task type / 参考标签规则卡。",
        "rounds": [
            {
                "http": "第 1 次请求",
                "stage": "r1_perceive_fuse",
                "role": "感知融合",
                "composed": "plan_r2va_r1",
                "set": "plan_r2va_r1",
                "cn": "资产清单、Subject、CAST、TASK TYPE、RETENTION PLAN、FUSED PROMPT。",
            },
            {
                "http": "第 2 次请求",
                "stage": "r2_plot",
                "role": "情节规划",
                "composed": "plan_r2va_r2",
                "set": "plan_r2va_r2",
                "cn": "确认 CAST / SHOT PLAN，写出完整 SCENE NOTE。",
            },
            {
                "http": "第 3 次请求",
                "stage": "r3_format_r2va",
                "role": "格式终轮",
                "composed": "format_r2va",
                "set": "format_r2va",
                "final": True,
                "cn": "六段：subject_definitions → … → non_diegetic_music。",
            },
        ],
    },
]


def _render_mode(mode: dict, data: dict) -> str:
    """渲染单个模式面板（静态 HTML，默认隐藏，首模式显示）。"""
    mid = mode["id"]
    active = " is-active" if mid == "t2va" else ""
    chips = "".join(_chip(s) for s in data["skill_sets"].get(mode["rounds"][-1]["set"], []))
    rounds_html: list[str] = []
    for i, rnd in enumerate(mode["rounds"]):
        open_attr = " open" if i == 0 else ""
        final = ' <span class="pill pill-ok">投片</span>' if rnd.get("final") else ""
        composed = data["composed"][rnd["composed"]]
        skills_body = data["skill_set_bodies"][rnd["set"]]
        set_chips = "".join(_chip(s) for s in data["skill_sets"][rnd["set"]])
        rounds_html.append(
            f"""
<details class="round"{open_attr}>
  <summary>
    <span class="pill">{html.escape(rnd["http"])}</span>
    <span class="round-title">{html.escape(rnd["stage"])} · {html.escape(rnd["role"])}</span>
    {final}
  </summary>
  <div class="round-body">
    <p class="round-cn">{html.escape(rnd["cn"])}</p>
    <p class="set-label">本轮规则卡</p>
    <div class="chip-row">{set_chips}</div>
    <div class="dual">
      {_block(
            "完整 SYSTEM（实际发给模型）",
            composed,
            note="英文原文已分段，可用沉浸式翻译整段翻译。",
        )}
      {_block(
            "规则卡集合正文",
            skills_body,
            note="仅本轮装配的 skills，不含阶段提示词。",
        )}
    </div>
  </div>
</details>
"""
        )
    return f"""
<section class="mode-panel{active}" id="panel-{html.escape(mid)}" data-mode="{html.escape(mid)}">
  <div class="mode-intro imt-block" translate="yes">
    <h2>{html.escape(mode["label"])}</h2>
    <p>{html.escape(mode["blurb"])}</p>
    <p class="meta-line">固定 <strong>{mode["rounds_n"]}</strong> 次 Gemini HTTP，终轮后本地清洗 + verify_local 告警。</p>
    <p class="set-label">格式终轮规则卡</p>
    <div class="chip-row">{chips}</div>
  </div>
  {"".join(rounds_html)}
</section>
"""


def _render_skills_index(skills: dict[str, str]) -> str:
    """渲染全部规则卡索引（静态可翻译）。"""
    items: list[str] = []
    for name, body in skills.items():
        items.append(
            f"""
<details class="skill-item">
  <summary><code class="skill-id notranslate">{html.escape(name)}</code></summary>
  <div class="imt-block" translate="yes">{_paras(body)}</div>
</details>
"""
        )
    return "\n".join(items)


def main() -> None:
    """写入优化后的 HTML 展示页。"""
    data = _collect()
    panels = "\n".join(_render_mode(m, data) for m in MODES)
    skills_index = _render_skills_index(data["skills"])
    tabs = "\n".join(
        f'<button type="button" class="tab{" is-active" if m["id"]=="t2va" else ""}" '
        f'data-mode="{html.escape(m["id"])}">{html.escape(m["label"])}</button>'
        for m in MODES
    )
    core_block = _block("_core.txt · 分层优先级", data["files"]["_core"])

    page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>agnes_context_ir · 系统逻辑与 H3 syntax skills</title>
<!-- 沉浸式翻译：显式声明可翻译区域，避免跳过动态/类代码块 -->
<script>
window.immersiveTranslateConfig = {{
  pageRule: {{
    selectors: [".imt-block", ".imt-body", ".imt-p", ".mode-intro", ".doc-card", ".round-cn"],
    excludeSelectors: ["nav", ".tab", ".chip", ".pill", ".skill-id", ".notranslate", "button", "script", "style"],
    additionalSelectors: [".imt-block p", "article.doc-card"],
    extraBlockSelectors: [".imt-p", ".doc-card__body", ".mode-intro"],
    preWhitespaceDetectedTags: [],
    paragraphMinTextCount: 8,
    paragraphMinWordCount: 2,
    observeUrlChange: true,
    urlChangeDelay: 400
  }}
}};
</script>
<style>
:root {{
  --bg: #f4f6f8;
  --surface: #ffffff;
  --surface-2: #eef2f6;
  --ink: #1a2332;
  --muted: #5a6b7d;
  --line: #d5dee8;
  --accent: #0d7c6f;
  --accent-soft: #e6f5f2;
  --blue: #2457c5;
  --blue-soft: #e8eefc;
  --ok: #1f7a3f;
  --ok-soft: #e7f6ec;
  --warn: #9a5b00;
  --shadow: 0 1px 2px rgba(26,35,50,.06), 0 8px 24px rgba(26,35,50,.06);
  --radius: 14px;
  --serif: "Source Serif 4", "Noto Serif SC", "Songti SC", Georgia, serif;
  --sans: "DM Sans", "PingFang SC", "Noto Sans SC", "Segoe UI", sans-serif;
  --mono: "JetBrains Mono", "IBM Plex Mono", ui-monospace, monospace;
}}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{
  margin: 0;
  color: var(--ink);
  font-family: var(--sans);
  background:
    radial-gradient(900px 420px at 0% -10%, #d9f3ef 0%, transparent 55%),
    radial-gradient(700px 380px at 100% 0%, #dfe8fb 0%, transparent 50%),
    var(--bg);
  line-height: 1.6;
}}
.shell {{
  max-width: 1120px;
  margin: 0 auto;
  padding: 28px 20px 72px;
}}
.hero {{
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 32px 32px 28px;
  box-shadow: var(--shadow);
  position: relative;
  overflow: hidden;
}}
.hero::after {{
  content: "";
  position: absolute; right: -40px; top: -40px;
  width: 180px; height: 180px; border-radius: 50%;
  background: var(--accent-soft); opacity: .7; pointer-events: none;
}}
.kicker {{
  margin: 0 0 10px;
  font-size: 12px; font-weight: 700; letter-spacing: .14em;
  text-transform: uppercase; color: var(--accent);
}}
.hero h1 {{
  margin: 0 0 12px;
  font-family: var(--serif);
  font-size: clamp(1.6rem, 3vw, 2.15rem);
  font-weight: 650; line-height: 1.2; letter-spacing: -.02em;
}}
.lede {{
  margin: 0;
  max-width: 62ch;
  color: var(--muted);
  font-size: 15.5px;
}}
.badge-row {{
  display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px;
}}
.badge {{
  font-size: 12px; padding: 6px 11px; border-radius: 999px;
  border: 1px solid var(--line); background: var(--surface-2); color: var(--muted);
}}
.badge-accent {{ background: var(--accent-soft); color: var(--accent); border-color: #b7e2db; }}
.badge-ok {{ background: var(--ok-soft); color: var(--ok); border-color: #b9e0c6; }}
.layout {{
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 20px;
  margin-top: 22px;
  align-items: start;
}}
@media (max-width: 860px) {{
  .layout {{ grid-template-columns: 1fr; }}
  .side {{ position: static !important; }}
}}
.side {{
  position: sticky; top: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 14px;
  box-shadow: var(--shadow);
}}
.side h2 {{
  margin: 0 0 10px;
  font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted);
}}
.tabs {{ display: flex; flex-direction: column; gap: 6px; }}
.tab {{
  appearance: none; text-align: left; width: 100%;
  border: 1px solid transparent; background: transparent;
  color: var(--ink); padding: 10px 12px; border-radius: 10px;
  font: 600 13px/1.35 var(--sans); cursor: pointer;
}}
.tab:hover {{ background: var(--surface-2); }}
.tab.is-active {{
  background: var(--blue-soft); border-color: #c5d4f5; color: var(--blue);
}}
.side-note {{
  margin: 14px 0 0; padding-top: 12px; border-top: 1px solid var(--line);
  font-size: 12.5px; color: var(--muted); line-height: 1.55;
}}
.main {{ min-width: 0; }}
.mode-panel {{ display: none; }}
.mode-panel.is-active {{ display: block; }}
.mode-intro, .panel {{
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 20px 22px;
  box-shadow: var(--shadow);
  margin-bottom: 14px;
}}
.mode-intro h2 {{
  margin: 0 0 8px;
  font-family: var(--serif);
  font-size: 1.35rem;
}}
.mode-intro p {{ margin: 0 0 8px; color: var(--muted); }}
.meta-line {{ color: var(--ink) !important; }}
.set-label {{
  margin: 12px 0 6px !important;
  font-size: 12px; font-weight: 700; letter-spacing: .06em;
  text-transform: uppercase; color: var(--muted) !important;
}}
.chip-row {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.chip {{
  font-family: var(--mono); font-size: 11px;
  padding: 4px 8px; border-radius: 8px;
  background: var(--blue-soft); color: var(--blue);
  border: 1px solid #c9d6f5;
}}
.round {{
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  margin-bottom: 12px;
  box-shadow: var(--shadow);
  overflow: hidden;
}}
.round > summary {{
  list-style: none; cursor: pointer;
  display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
  padding: 14px 16px;
  background: linear-gradient(90deg, var(--surface-2), var(--surface));
}}
.round > summary::-webkit-details-marker {{ display: none; }}
.round[open] > summary {{ border-bottom: 1px solid var(--line); }}
.round-title {{ font-weight: 650; font-size: 14px; }}
.pill {{
  font-family: var(--mono); font-size: 11px;
  padding: 4px 8px; border-radius: 999px;
  background: var(--accent-soft); color: var(--accent);
  border: 1px solid #b7e2db;
}}
.pill-ok {{ background: var(--ok-soft); color: var(--ok); border-color: #b9e0c6; }}
.round-body {{ padding: 14px 16px 18px; }}
.round-cn {{ margin: 0 0 10px; color: var(--muted); font-size: 14px; }}
.dual {{
  display: flex;
  flex-direction: column;
  gap: 14px;
}}
.doc-card {{
  border: 1px solid var(--line);
  border-radius: 12px;
  background: #fbfcfd;
  overflow: hidden;
  min-width: 0;
}}
.doc-card__head {{
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
  background: var(--surface-2);
}}
.doc-card__title {{
  margin: 0;
  font-size: 13px;
  font-weight: 700;
}}
.block-note {{
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--muted);
}}
.doc-card__body {{
  padding: 12px 14px 14px;
  max-height: 480px;
  overflow: auto;
}}
.imt-p {{
  margin: 0 0 12px;
  font-size: 13.5px;
  line-height: 1.65;
  color: var(--ink);
  white-space: normal;
  word-break: break-word;
}}
.imt-p:last-child {{ margin-bottom: 0; }}
.panel h2 {{
  margin: 0 0 6px;
  font-family: var(--serif);
  font-size: 1.2rem;
}}
.panel > .lede {{ margin-bottom: 12px; }}
.skill-item {{
  border: 1px solid var(--line);
  border-radius: 10px;
  margin-bottom: 8px;
  background: #fbfcfd;
}}
.skill-item > summary {{
  list-style: none; cursor: pointer; padding: 10px 12px;
}}
.skill-item > summary::-webkit-details-marker {{ display: none; }}
.skill-item[open] > summary {{ border-bottom: 1px solid var(--line); }}
.skill-id {{
  font-family: var(--mono); font-size: 12.5px; color: var(--blue);
}}
.skill-item .imt-block {{ padding: 12px 14px; }}
.formula {{
  font-family: var(--mono); font-size: 12.5px;
  background: #0f1720; color: #d7e6f5;
  border-radius: 10px; padding: 12px 14px;
  overflow-x: auto; margin: 10px 0;
  white-space: pre-wrap;
}}
.foot {{
  margin-top: 28px; padding-top: 14px;
  border-top: 1px solid var(--line);
  color: var(--muted); font-size: 12.5px;
}}
.tip {{
  margin-top: 12px; padding: 12px 14px;
  border-radius: 10px;
  background: #fff8e8; border: 1px solid #f0d9a0;
  color: var(--warn); font-size: 13px;
}}
</style>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,600;0,9..40,700;1,9..40,400&family=Source+Serif+4:opsz,wght@8..60,600;8..60,650&display=swap" rel="stylesheet" />
</head>
<body>
<div class="shell">
  <header class="hero imt-block" translate="yes">
    <p class="kicker">agnes_context_ir</p>
    <h1>系统逻辑与每次 HTTP 的 SYSTEM</h1>
    <p class="lede">
      保持 2～3 次模型调用不变。SYSTEM = 分层 _core + 阶段提示词 + 按模式装配的 H3 语法规则卡 + 单个官方示例。
      下方英文正文已拆成普通段落，可用「沉浸式翻译」直接翻译。
    </p>
    <div class="badge-row">
      <span class="badge badge-accent">skills 替换全文指南</span>
      <span class="badge">语法层官方优先 · 内容层用户锁定</span>
      <span class="badge badge-ok">verify_local 只告警</span>
    </div>
    <p class="tip">
      翻译提示：点沉浸式翻译的「翻译网页」。若仍不翻，到插件设置 → 开发者设置，确认未排除本地 file://；
      本页已通过 immersiveTranslateConfig 声明 .imt-block 为翻译目标，并避免用 pre/code 包裹正文。
    </p>
  </header>

  <div class="layout">
    <aside class="side">
      <h2>模式</h2>
      <nav class="tabs" id="modeTabs" aria-label="模式切换">
        {tabs}
      </nav>
      <p class="side-note">正文在构建时已写入 HTML，切换模式只改显示，不重新拉 JSON。翻完一个模式后切换，可能需再点一次翻译。</p>
    </aside>

    <main class="main">
      {panels}

      <section class="panel imt-block" translate="yes">
        <h2>SYSTEM 拼装公式</h2>
        <p class="lede">来自 compose_format_system / compose_plan_system。</p>
        <div class="formula notranslate">格式轮 = _core + 阶段提示词
         + H3 syntax skills (binding)
         + Official example (1 Case)

规划轮 = _core + 阶段提示词
         + H3 syntax skills (planning subset)</div>
        {core_block}
      </section>

      <section class="panel">
        <h2 class="imt-block" translate="yes">全部规则卡索引</h2>
        <p class="lede imt-block" translate="yes">点击展开单张 skill；正文同样可被沉浸式翻译处理。</p>
        {skills_index}
      </section>
    </main>
  </div>

  <footer class="foot imt-block" translate="yes">
    由 tools/build_viewer.py 生成 · 直接用浏览器打开本文件即可，无需端口。
    重新生成：<span class="notranslate">python3 tools/build_viewer.py</span>
  </footer>
</div>
<script>
(function () {{
  const tabs = document.querySelectorAll(".tab");
  const panels = document.querySelectorAll(".mode-panel");
  function activate(mode) {{
    tabs.forEach((t) => t.classList.toggle("is-active", t.dataset.mode === mode));
    panels.forEach((p) => p.classList.toggle("is-active", p.dataset.mode === mode));
    // 通知可能监听 DOM 的翻译扩展：内容区已切换
    document.dispatchEvent(new CustomEvent("agnes-viewer-mode", {{ detail: {{ mode }} }}));
  }}
  tabs.forEach((t) => t.addEventListener("click", () => activate(t.dataset.mode)));
}})();
</script>
</body>
</html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8", newline="\n")
    if b"\r\n" in OUT.read_bytes():
        raise SystemExit("CRLF detected")
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
