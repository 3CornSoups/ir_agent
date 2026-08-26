"""T0.2 保真检查器单测：10 项 FC、已知违规召回、合规零误报。"""

from __future__ import annotations

import json

from src.contract import DialogueLine, IntentContract, RefAttr, ShotConstraint, parse_intent_deterministic
from src.fidelity import (
    evaluate_fidelity,
    fc1_must_coverage,
    fc2_forbidden,
    fc3_dialogue_verbatim,
    fc4_onscreen_verbatim,
    fc5_action_order,
    fc6_reference_attrs,
    fc7_shot_constraint,
    fc8_timestamp_bounds,
    fc9_invention,
    fc10_canvas_leak,
    fidelity_pass_rate,
    invention_rate,
)

GOOD_BASE = """integrated_multimodal_description: [Shot 1] An orange cat stretches on a wooden windowsill in morning sun. The camera pushes in slowly.

overall_soundscape: Soft room tone and distant birds.

non_diegetic_music: N/A"""


def _chat_fixed(value: int):
    """构造固定返回 value 的 mock chat。"""

    def _chat(system: str, user: str, *, stage: str = "") -> str:
        if "list_inventions" in user:
            return json.dumps({"inventions": [] if value == 0 else ["invented logo"]})
        return json.dumps({"value": value})

    return _chat


def test_fc1_missing_must() -> None:
    """FC1：缺 must 元素应失败。"""
    c = IntentContract(must_elements=["橘猫", "窗台"], intent_raw="x")
    r = fc1_must_coverage(c, "A dog runs in a park.")
    assert not r.passed
    assert "橘猫" in r.violations or "窗台" in r.violations


def test_fc1_covered() -> None:
    """FC1：must 子串全覆盖应通过。"""
    c = IntentContract(must_elements=["orange cat", "windowsill"], intent_raw="x")
    r = fc1_must_coverage(c, GOOD_BASE)
    assert r.passed and r.score == 1.0


def test_fc2_single_shot_multi() -> None:
    """FC2/FC7：单镜头约束下多 Shot 应 hard-fail。"""
    c = IntentContract(
        forbidden=["禁止切镜"],
        shot_constraint=ShotConstraint(single_shot=True, max_shots=1),
        intent_raw="禁止切镜",
    )
    prompt = GOOD_BASE.replace(
        "[Shot 1] An orange cat",
        "[Shot 1] An orange cat\n[Shot 2] At 00:03.000, cut to the tail",
    )
    r2 = fc2_forbidden(c, prompt)
    r7 = fc7_shot_constraint(c, prompt)
    assert not r7.passed and r7.hard_fail
    assert not r2.passed


def test_fc2_no_dialogue_ignores_ambience_chatter() -> None:
    """FC2：无对白约束下，环境 indistinct chatter 不算违反。"""
    c = IntentContract(forbidden=["无对白"], intent_raw="雨夜路口，无对白")
    ambient = (
        GOOD_BASE
        + "\noverall_soundscape: rain and faint, blurred human chatter.\n\n"
        "non_diegetic_music: N/A"
    )
    assert fc2_forbidden(c, ambient).passed
    spoken = (
        "integrated_multimodal_description: [Shot 1] She says hello to the crowd.\n\n"
        "overall_soundscape: voice\n\nnon_diegetic_music: N/A"
    )
    assert not fc2_forbidden(c, spoken).passed


def test_fc2_daytime_sky_forbid_on_dusk_prompt() -> None:
    """FC2：禁止白天晴空时，黄昏逆光稿应合规。"""
    c = IntentContract(forbidden=["白天晴空", "卡通/动画风格"], intent_raw="逆光黄昏，禁止白天晴空")
    dusk = (
        "integrated_multimodal_description: [Shot 1] Live-action cinematic dusk silhouette "
        "on a pier, golden rim light, sunset near the horizon, shallow depth of field.\n\n"
        "overall_soundscape: waves\n\nnon_diegetic_music: N/A"
    )
    assert fc2_forbidden(c, dusk).passed
    day = (
        "integrated_multimodal_description: [Shot 1] Bright midday sun under a clear blue sky.\n\n"
        "overall_soundscape: birds\n\nnon_diegetic_music: N/A"
    )
    assert not fc2_forbidden(c, day).passed


def test_fc3_dialogue_must_in_d_tag() -> None:
    """FC3：对白不在 <d> 内应失败。"""
    c = IntentContract(
        dialogue=[DialogueLine(text="你来了。剑等得够久了。")],
        intent_raw="她说：『你来了。剑等得够久了。』",
    )
    bad = GOOD_BASE + "\nShe says: you came."
    assert not fc3_dialogue_verbatim(c, bad).passed
    good = (
        "integrated_multimodal_description: [Shot 1] She whispers "
        "<d>[Chinese]你来了。剑等得够久了。</d> softly.\n\n"
        "overall_soundscape: Her voice.\n\nnon_diegetic_music: N/A"
    )
    assert fc3_dialogue_verbatim(c, good).passed


def test_fc3_mandarin_tag_fails() -> None:
    """FC3：使用 [Mandarin] 应失败。"""
    c = IntentContract(dialogue=[DialogueLine(text="你好")], intent_raw="「你好」")
    prompt = (
        "integrated_multimodal_description: [Shot 1] <d>[Mandarin]你好</d>\n\n"
        "overall_soundscape: voice\n\nnon_diegetic_music: N/A"
    )
    assert not fc3_dialogue_verbatim(c, prompt).passed


def test_fc4_onscreen_missing() -> None:
    """FC4：屏上口号缺失应失败。"""
    c = IntentContract(onscreen_text=["让每一步都算数"], intent_raw="口号「让每一步都算数」")
    assert not fc4_onscreen_verbatim(c, GOOD_BASE).passed
    good = GOOD_BASE.replace(
        "morning sun.",
        'morning sun. On-screen text reads "让每一步都算数".',
    )
    assert fc4_onscreen_verbatim(c, good).passed


def test_fc5_action_order() -> None:
    """FC5：动作链乱序/缺失应失败；保序通过。"""
    c = IntentContract(action_chain=["停球", "转身", "射门"], intent_raw="x")
    ordered = "[Shot 1] 停球 then 转身 then 射门"
    assert fc5_action_order(c, ordered).passed
    shuffled = "[Shot 1] 射门 then 停球 then 转身"
    assert not fc5_action_order(c, shuffled).passed
    missing = "[Shot 1] 停球 then 射门"
    assert not fc5_action_order(c, missing).passed


def test_fc6_ref_attr_retention() -> None:
    """FC6：identity 属性丢失拉低保留率。"""
    c = IntentContract(
        reference_attrs=[
            RefAttr("<Picture 1>", "红色连帽卫衣", "identity"),
            RefAttr("<Picture 1>", "黑色长裤", "identity"),
        ],
        intent_raw="x",
    )
    bad = "[Shot 1] A person walks shirtless."
    r = fc6_reference_attrs(c, bad)
    assert not r.passed and r.score < 0.9
    good = "[Shot 1] Wearing 红色连帽卫衣 and 黑色长裤."
    assert fc6_reference_attrs(c, good).passed


def test_fc8_timestamp_over_duration() -> None:
    """FC8：时间戳超时长或非单调应失败。"""
    c = IntentContract(duration_sec=5.0, intent_raw="约5秒")
    over = "[Shot 1] At 00:00.000, start. [Shot 2] At 00:09.000, end."
    assert not fc8_timestamp_bounds(c, over).passed
    nonmono = "[Shot 1] At 00:03.000, a. [Shot 2] At 00:01.000, b."
    assert not fc8_timestamp_bounds(c, nonmono).passed


def test_fc9_person_invention_heuristic() -> None:
    """FC9：禁止人物却写 person 应计发明。"""
    c = IntentContract(forbidden=["禁止人物入镜"], intent_raw="禁止人物入镜")
    bad = "[Shot 1] A woman pours coffee."
    r = fc9_invention(c, bad)
    assert not r.passed and r.score >= 1


def test_fc9_filters_non_significant_inventions() -> None:
    """FC9：光影/环境声等 Inferred 细节不计显著发明。"""
    from src.fidelity import _filter_significant_inventions

    kept = _filter_significant_inventions(
        [
            "soft ambient soundscape",
            "bell-like resonant tone",
            "invented brand logo",
            "specular wood grain sheen",
        ]
    )
    assert kept == ["invented brand logo"]


def test_fc10_canvas_leak() -> None:
    """FC10：画幅/分辨率泄漏应 hard-fail。"""
    assert not fc10_canvas_leak("16:9 aspect ratio, 768P").passed
    assert fc10_canvas_leak(GOOD_BASE).passed


def test_evaluate_pass_formula() -> None:
    """case 级 F_pass 公式：全硬约束 + FC1/5/6/9。"""
    c = parse_intent_deterministic("一只橘猫在窗台晒太阳，禁止切镜，约5秒")
    # 补 must 以便覆盖检查
    c.must_elements = ["orange cat", "windowsill"]
    ok = evaluate_fidelity(c, GOOD_BASE)
    assert ok.passed
    bad = evaluate_fidelity(
        c,
        GOOD_BASE.replace("[Shot 1]", "[Shot 1] x\n[Shot 2] At 00:03.000, y"),
    )
    assert not bad.passed


def test_ten_known_violations_all_caught() -> None:
    """构造 10 个已知违规样本，全部被正确捕获（召回 100%）。"""
    samples: list[tuple[str, IntentContract, str]] = []
    # 1 FC1
    samples.append(
        (
            "FC1",
            IntentContract(must_elements=["橘猫"], intent_raw="橘猫"),
            "A bird flies.",
        )
    )
    # 2 FC3
    samples.append(
        (
            "FC3",
            IntentContract(dialogue=[DialogueLine(text="你好")], intent_raw="「你好」"),
            GOOD_BASE,
        )
    )
    # 3 FC4
    samples.append(
        (
            "FC4",
            IntentContract(onscreen_text=["让每一步都算数"], intent_raw="口号「让每一步都算数」"),
            GOOD_BASE,
        )
    )
    # 4 FC5
    samples.append(
        (
            "FC5",
            IntentContract(action_chain=["停球", "转身", "射门"], intent_raw="x"),
            "射门 then 停球",
        )
    )
    # 5 FC6
    samples.append(
        (
            "FC6",
            IntentContract(
                reference_attrs=[RefAttr("<Picture 1>", "红色连帽卫衣", "identity")],
                intent_raw="x",
            ),
            "shirtless man",
        )
    )
    # 6 FC7
    samples.append(
        (
            "FC7",
            IntentContract(shot_constraint=ShotConstraint(single_shot=True, max_shots=1), intent_raw="单镜头"),
            "[Shot 1] a [Shot 2] b",
        )
    )
    # 7 FC8
    samples.append(
        (
            "FC8",
            IntentContract(duration_sec=4.0, intent_raw="约4秒"),
            "[Shot 1] At 00:00.000, a. [Shot 2] At 00:08.000, b.",
        )
    )
    # 8 FC9
    samples.append(
        (
            "FC9",
            IntentContract(forbidden=["禁止人物入镜"], intent_raw="禁止人物入镜"),
            "A man stands here.",
        )
    )
    # 9 FC10
    samples.append(
        (
            "FC10",
            IntentContract(intent_raw="x"),
            "cinematic 16:9 aspect ratio shot",
        )
    )
    # 10 FC2 music
    samples.append(
        (
            "FC2",
            IntentContract(forbidden=["不要配乐"], intent_raw="不要配乐"),
            GOOD_BASE.replace("non_diegetic_music: N/A", "non_diegetic_music: Loud orchestra score"),
        )
    )

    assert len(samples) == 10
    for code, contract, prompt in samples:
        report = evaluate_fidelity(contract, prompt)
        assert not report.passed or not report.checks[code].passed, f"{code} 应被捕获"
        assert not report.checks[code].passed, f"{code} 单项应失败"


def test_ten_compliant_zero_false_positive() -> None:
    """10 个已知合规样本：对应 FC 零误报。"""
    cases: list[tuple[str, IntentContract, str]] = [
        (
            "FC1",
            IntentContract(must_elements=["orange cat"], intent_raw="x"),
            GOOD_BASE,
        ),
        (
            "FC3",
            IntentContract(dialogue=[DialogueLine(text="你好")], intent_raw="「你好」"),
            "integrated_multimodal_description: [Shot 1] <d>[Chinese]你好</d>\n\n"
            "overall_soundscape: voice\n\nnon_diegetic_music: N/A",
        ),
        (
            "FC4",
            IntentContract(onscreen_text=["让每一步都算数"], intent_raw="口号「让每一步都算数」"),
            GOOD_BASE.replace("sun.", 'sun. Text "让每一步都算数".'),
        ),
        (
            "FC5",
            IntentContract(action_chain=["停球", "转身", "射门"], intent_raw="x"),
            "停球 → 转身 → 射门 complete",
        ),
        (
            "FC6",
            IntentContract(
                reference_attrs=[RefAttr("<Picture 1>", "红色连帽卫衣", "identity")],
                intent_raw="x",
            ),
            "She wears 红色连帽卫衣 walking.",
        ),
        (
            "FC7",
            IntentContract(shot_constraint=ShotConstraint(single_shot=True, max_shots=1), intent_raw="单镜头"),
            GOOD_BASE,
        ),
        (
            "FC8",
            IntentContract(duration_sec=6.0, intent_raw="约6秒"),
            "[Shot 1] At 00:00.000, a. [Shot 2] At 00:04.000, b.",
        ),
        (
            "FC9",
            IntentContract(forbidden=["禁止人物入镜"], intent_raw="禁止人物入镜"),
            "[Shot 1] Coffee pours into a cup. Empty kitchen counter.",
        ),
        (
            "FC10",
            IntentContract(intent_raw="x"),
            GOOD_BASE,
        ),
        (
            "FC2",
            IntentContract(forbidden=["不要配乐"], intent_raw="不要配乐"),
            GOOD_BASE,
        ),
    ]
    assert len(cases) == 10
    for code, contract, prompt in cases:
        report = evaluate_fidelity(contract, prompt)
        assert report.checks[code].passed, f"{code} 合规却误报: {report.checks[code]}"


def test_llm_paths_independent_calls() -> None:
    """LLM 路径：FC1 对每个 must 独立提问。"""
    calls: list[str] = []

    def chat(system: str, user: str, *, stage: str = "") -> str:
        calls.append(user)
        # 只覆盖第一个元素
        if "窗台" in user and "must_covered" in user:
            return json.dumps({"value": 0})
        if "list_inventions" in user:
            return json.dumps({"inventions": []})
        return json.dumps({"value": 1})

    c = IntentContract(must_elements=["橘猫", "窗台"], intent_raw="橘猫窗台")
    r = fc1_must_coverage(c, "有橘猫", chat=chat)
    assert not r.passed
    assert sum("must_covered" in u for u in calls) == 2


def test_deterministic_stable_three_runs() -> None:
    """同输入跑 3 次确定性 FC 结果一致。"""
    c = parse_intent_deterministic("球员停球→转身→射门，禁止切镜，约6秒")
    prompt = "[Shot 1] 停球 then 转身 then 射门. 16:9 aspect ratio"
    results = [evaluate_fidelity(c, prompt).to_dict() for _ in range(3)]
    assert results[0] == results[1] == results[2]


def test_aggregate_rates() -> None:
    """集合级通过率 / 发明率。"""
    c_ok = IntentContract(must_elements=["orange cat"], intent_raw="x", duration_sec=5)
    c_bad = IntentContract(forbidden=["禁止人物入镜"], intent_raw="禁止人物入镜")
    reports = [
        evaluate_fidelity(c_ok, GOOD_BASE),
        evaluate_fidelity(c_bad, "A woman smiles."),
    ]
    assert 0.0 <= fidelity_pass_rate(reports) <= 1.0
    assert invention_rate(reports) >= 0.5
