# -*- coding: utf-8 -*-
"""Agent vs 官方 Context-IR 管线的对比测试。

层次：
1. 用例素材存在性 —— 用例清单引用的 zwb 资产/官方输出/本地 Qwen 输出/成片都存在
2. agent prompt 结构 —— mock chat 跑 enhance，验证产物符合官方字段骨架、无画幅残留
   （fl2va/l2va 为官方独有模式，agent 暂不覆盖，自动跳过）
3. 官方 IR 输出结构对齐 —— 字段骨架、帧对齐句、画幅残留
4. 本地 Qwen 增强稿结构对照 —— 作为 agent 同源参照管线，验证同一骨架
5. 成片资产存在性 —— 官方/本地双管线成片都在
6. 输入素材 → build_content 结构
7. 用例清单自检

运行（默认只跑非出片测试，不调用本地模型）：
    cd /kwkj-k8s/zwb/项目/agent/new_agent0818 && pytest -q test/test_agent_vs_official.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cases_agent_vs_official import (
    FORBIDDEN_TOKENS,
    align_hint,
    build_cases,
    case_by_id,
    expected_fields,
    is_frame_aligned,
)
from src.pipeline import enhance
from src.video import build_content


def _read(path: str) -> str:
    """读取文本文件并 strip。"""
    return Path(path).read_text(encoding="utf-8").strip()


def _has_fields(text: str, fields: tuple[str, ...]) -> bool:
    """字段名是否都以「行首 字段名:」形式出现。"""
    for f in fields:
        if not (text.startswith(f"{f}:") or f"\n{f}:" in text):
            return False
    return True


# ---------------------------------------------------------------------------
# 1) 用例素材存在性
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_id", [c["id"] for c in build_cases()])
def test_case_asset_files_exist(case_id: str) -> None:
    """用例引用的意图/素材/官方输出/本地 Qwen 输出/成片都应存在。"""
    case = case_by_id(case_id)
    # 意图是内嵌文本（build_cases 里已读），其余是路径。
    for key in (
        "first_frame",
        "last_frame",
        "official_prompt",
        "cmp_official_prompt",
        "local_qwen_prompt",
        "agent_run_prompt",
        "official_video",
        "local_qwen_video",
    ):
        value = case.get(key)
        if value:
            assert Path(value).is_file(), f"{case_id}.{key} 缺失: {value}"
    for key in ("reference_images", "reference_videos", "reference_audios"):
        for p in case.get(key) or []:
            assert Path(p).is_file(), f"{case_id}.{key} 缺失: {p}"


@pytest.mark.parametrize("case_id", [c["id"] for c in build_cases()])
def test_case_media_valid_for_build_content(case_id: str) -> None:
    """帧对齐模式必须有首/尾帧；r2va 必须有参考图/视频（对应 build_content 前置约束）。"""
    case = case_by_id(case_id)
    mode = case["mode"]
    if mode == "i2va":
        assert case.get("first_frame"), f"{case_id}: i2va 需要 first_frame"
    if mode == "fl2va":
        assert case.get("first_frame") and case.get("last_frame"), f"{case_id}: fl2va 需要首尾帧"
    if mode == "l2va":
        assert case.get("last_frame"), f"{case_id}: l2va 需要 last_frame"
    if mode == "r2va":
        assert case.get("reference_images") or case.get("reference_videos"), \
            f"{case_id}: r2va 需要至少一张参考图或一段参考视频"


# ---------------------------------------------------------------------------
# 2) agent prompt 结构（mocked chat，不调用 Gemini / 本地模型）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_id", [c["id"] for c in build_cases()])
def test_agent_prompt_structure_mocked(case_id: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """mock chat 跑 enhance：产物字段骨架正确、无画幅/分辨率残留。"""
    case = case_by_id(case_id)
    if not case.get("agent_supported", True):
        pytest.skip(f"{case_id}: 模式 {case['mode']} 为官方独有，agent 暂不覆盖")
    import src.pipeline as pipeline_mod

    def load_prompt_mock(stem: str) -> str:
        return f"SYS_{stem}\n"

    def chat_mock(system: str, user, *, stage: str = "expand") -> str:  # noqa: ANN001
        if stage == "perceive":
            return "INVENTORY: <Picture 1> 人物外观; <Video 1> 动作节奏"
        if stage == "expand":
            return "EXPANDED SCENE 16:9 768P"
        fields = expected_fields(case["mode"])
        return "\n\n".join(f"{f}: [Shot 1] placeholder 16:9 768P 24fps" for f in fields)

    monkeypatch.setattr(pipeline_mod, "load_prompt", load_prompt_mock)
    monkeypatch.setattr(pipeline_mod, "chat", chat_mock)

    out_dir = tmp_path / f"out_{case_id}"
    rec = enhance(
        case["mode"],
        case["intent"],
        first_frame=case.get("first_frame"),
        reference_images=case.get("reference_images"),
        reference_videos=case.get("reference_videos"),
        reference_audios=case.get("reference_audios"),
        duration=case.get("duration"),
        out_dir=out_dir,
    )

    prompt = rec["prompt"]
    assert rec["mode"] == case["mode"]
    assert _has_fields(prompt, expected_fields(case["mode"])), f"{case_id}: 缺字段骨架"
    # agent 特有约束：画幅/分辨率/帧率不得写入 prompt
    for token in FORBIDDEN_TOKENS:
        assert token not in prompt, f"{case_id}: prompt 里出现不应有的 {token!r}"


# ---------------------------------------------------------------------------
# 3) 官方 Context-IR 输出结构对齐
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "case_id",
    [c["id"] for c in build_cases() if c.get("official_prompt")],
)
def test_official_ir_prompt_skeleton(case_id: str) -> None:
    """官方 IR 输出字段骨架应符合对应模式（agent 对齐的基准）。"""
    case = case_by_id(case_id)
    text = _read(case["official_prompt"])
    assert _has_fields(text, expected_fields(case["mode"])), f"{case_id}: 官方 IR 缺字段骨架"


@pytest.mark.parametrize(
    "case_id",
    [c["id"] for c in build_cases() if c.get("official_prompt") and is_frame_aligned(c["mode"])],
)
def test_official_frame_aligned_hint(case_id: str) -> None:
    """帧对齐模式（i2va/fl2va/l2va）的官方稿头部应有对应对齐句。"""
    case = case_by_id(case_id)
    text = _read(case["official_prompt"])
    hint = align_hint(case["mode"])
    assert hint in text.split("\n\n")[0], f"{case_id}: 官方 {case['mode']} 稿缺对齐句特征 {hint!r}"


@pytest.mark.parametrize(
    "case_id",
    [c["id"] for c in build_cases() if c.get("official_prompt")],
)
def test_agent_vs_official_ratio_tokens(case_id: str) -> None:
    """
    对比 agent 与官方 IR 对画幅/分辨率字段的处置：
    agent 的 strip_canvas 承诺清掉，官方 IR 可能保留（这是两者的结构性差异）。
    本测试只报告官方残留，不强制官方也清除。
    """
    case = case_by_id(case_id)
    official = _read(case["official_prompt"])
    official_has = [t for t in FORBIDDEN_TOKENS if t in official]
    if official_has:
        print(f"[{case_id}] 官方 IR 输出含画幅/分辨率标记: {official_has}")


# ---------------------------------------------------------------------------
# 4) 本地 Qwen 增强稿结构对照（agent 同源参照管线）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "case_id",
    [c["id"] for c in build_cases() if c.get("local_qwen_prompt")],
)
def test_local_qwen_prompt_skeleton(case_id: str) -> None:
    """本地 Qwen 增强稿（intent_v1.1 / intent_r2va_v1）也应符合同一字段骨架。"""
    case = case_by_id(case_id)
    text = _read(case["local_qwen_prompt"])
    assert _has_fields(text, expected_fields(case["mode"])), f"{case_id}: 本地 Qwen 稿缺字段骨架"


@pytest.mark.parametrize(
    "case_id",
    [c["id"] for c in build_cases() if c.get("official_prompt") and c.get("local_qwen_prompt")],
)
def test_qwen_vs_official_density_report(case_id: str) -> None:
    """本地 Qwen 稿与官方稿的字数密度比（报告不强制）。"""
    case = case_by_id(case_id)
    official = _read(case["official_prompt"])
    qwen = _read(case["local_qwen_prompt"])
    ratio = len(qwen) / max(len(official), 1)
    print(f"[{case_id}] 字数密度 Qwen/官方 = {len(qwen)}/{len(official)} = {ratio:.2f}")


# ---------------------------------------------------------------------------
# 5) 成片资产存在性
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "case_id",
    [c["id"] for c in build_cases() if c.get("official_video")],
)
def test_official_videos_exist(case_id: str) -> None:
    """官方成片应存在且非空。"""
    case = case_by_id(case_id)
    p = Path(case["official_video"])
    assert p.is_file() and p.stat().st_size > 1024, f"{case_id}: 官方成片异常: {p}"


@pytest.mark.parametrize(
    "case_id",
    [c["id"] for c in build_cases() if c.get("local_qwen_video")],
)
def test_local_qwen_videos_exist(case_id: str) -> None:
    """本地 Qwen 成片应存在且非空。"""
    case = case_by_id(case_id)
    p = Path(case["local_qwen_video"])
    assert p.is_file() and p.stat().st_size > 1024, f"{case_id}: 本地 Qwen 成片异常: {p}"


# ---------------------------------------------------------------------------
# 6) 输入素材 → build_content 结构（agent 支持的 i2va/r2va）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "case_id",
    [c["id"] for c in build_cases() if c["mode"] in ("i2va", "r2va")],
)
def test_build_content_from_case_assets(case_id: str) -> None:
    """用用例素材直接构造 H3 content，确认 role/type 正确。"""
    case = case_by_id(case_id)
    content = build_content(
        case["mode"],
        "PROMPT_PLACEHOLDER",
        first_frame=case.get("first_frame"),
        reference_images=case.get("reference_images"),
        reference_videos=case.get("reference_videos"),
        reference_audios=case.get("reference_audios"),
    )
    assert content[0]["type"] == "text"
    roles = [p.get("role") for p in content if p.get("type") != "text"]

    if case["mode"] == "i2va":
        assert "first_frame" in roles
    if case["mode"] == "r2va":
        if case.get("reference_images"):
            assert "reference_image" in roles
        if case.get("reference_videos"):
            assert "reference_video" in roles


# ---------------------------------------------------------------------------
# 7) 用例清单自检
# ---------------------------------------------------------------------------
def test_cases_modes_and_durations_valid() -> None:
    """模式合法、时长在 4~15、画幅可解析。"""
    from src.video import ALL_RATIOS, T2VA_RATIOS

    for c in build_cases():
        assert c["mode"] in {"t2va", "i2va", "fl2va", "l2va", "r2va"}, c["id"]
        assert 4 <= c["duration"] <= 15, f"{c['id']} duration={c['duration']} 超出 4~15"
        ratio = c.get("ratio")
        if ratio:
            assert ratio in ALL_RATIOS, f"{c['id']} ratio={ratio} 非法"


def test_agent_supported_flags_consistent() -> None:
    """agent_supported 与 agent 实际支持的模式一致（t2va/i2va/r2va）。"""
    for c in build_cases():
        expected = c["mode"] in ("t2va", "i2va", "r2va")
        assert c.get("agent_supported") == expected, f"{c['id']} agent_supported 不一致"


def test_all_official_outputs_listed() -> None:
    """zwb 里已有的官方 IR 输出（R2VA 对照项目 + gold_ir），都应能在用例清单中找到。"""
    import glob
    import os

    from cases_agent_vs_official import ZW

    official_files = glob.glob(str(ZW / "项目" / "R2VA_Qwen与官方对照" / "*" / "提示词" / "official_*.txt"))
    listed = set()
    for c in build_cases():
        for key in ("official_prompt", "cmp_official_prompt"):
            if c.get(key):
                listed.add(os.path.abspath(c[key]))
    for f in official_files:
        assert os.path.abspath(f) in listed, f"官方输出未列入用例: {f}"


def test_gold_ir_and_gold_ir_r2va_all_listed() -> None:
    """母仓 gold_ir / gold_ir_r2va 的官方稿都应在用例清单中。"""
    from cases_agent_vs_official import GOLD_IR, GOLD_IR_R2VA

    listed = {Path(c["official_prompt"]).resolve() for c in build_cases() if c.get("official_prompt")}
    for d in (GOLD_IR, GOLD_IR_R2VA):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.txt")):
            assert p.resolve() in listed, f"gold_ir 官方稿未列入用例: {p}"
