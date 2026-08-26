"""多人物台词 / 字幕 / 独白保真（T-D1）单测。"""

from __future__ import annotations

from src.contract import parse_intent_deterministic
from src.fidelity import (
    evaluate_fidelity,
    fc3_dialogue_verbatim,
    fc11_dialogue_closed_world,
    fc12_subtitle_policy,
    fc13_monologue_closed_lips,
)


def test_extract_speakers_and_delivery() -> None:
    """双人质问应抽出 speaker；内心独白为 internal。"""
    intent = (
        "短发女高管尖锐地质问「你凭什么擅自拍板？」；"
        "长发女高管冷冷回应「结果说话，别再扯皮。」"
    )
    c = parse_intent_deterministic(intent, mode="fl2va")
    assert len(c.dialogue) >= 2
    assert "短发" in c.dialogue[0].speaker
    assert "长发" in c.dialogue[1].speaker
    assert c.dialogue[0].delivery == "spoken"

    c2 = parse_intent_deterministic(
        "女主内心独白「我必须离开」，不要说出口，不要张嘴，约5秒",
        mode="t2va",
    )
    assert c2.dialogue and c2.dialogue[0].text == "我必须离开"
    assert c2.dialogue[0].delivery == "internal"


def test_subtitle_policy_none() -> None:
    """全程不要字幕 → subtitle_policy=none。"""
    c = parse_intent_deterministic("雨夜街道，全程不要字幕，约6秒", mode="t2va")
    assert c.subtitle_policy == "none"
    assert any("字幕" in f for f in c.forbidden)


def test_fc3_speaker_binding_and_swap_fail() -> None:
    """说话人绑对通过；绑错失败。"""
    intent = (
        "短发女高管尖锐地质问「你凭什么擅自拍板？」；"
        "长发女高管冷冷回应「结果说话，别再扯皮。」"
    )
    c = parse_intent_deterministic(intent, mode="fl2va")
    ok = (
        'The short-haired executive (S1) shouts: <d>[Chinese] 你凭什么擅自拍板？</d> '
        'The long-haired executive (S2) responds: <d>[Chinese] 结果说话，别再扯皮。</d>'
    )
    assert fc3_dialogue_verbatim(c, ok).passed
    swapped = (
        'The short-haired executive (S1) shouts: <d>[Chinese] 结果说话，别再扯皮。</d> '
        'The long-haired executive (S2) responds: <d>[Chinese] 你凭什么擅自拍板？</d>'
    )
    # 文本都在 <d> 内，但 (S1) 邻域对应了错误台词 → speaker_unbound
    bad = fc3_dialogue_verbatim(c, swapped)
    assert not bad.passed


def test_fc11_rejects_extra_dialogue() -> None:
    """擅自增加 <d> 台词应失败。"""
    c = parse_intent_deterministic('角色说「你好」约4秒', mode="t2va")
    good = 'She (S1) says: <d>[Chinese] 你好</d>'
    assert fc11_dialogue_closed_world(c, good).passed
    bad = good + ' He replies: <d>[Chinese] 再见啦</d>'
    assert not fc11_dialogue_closed_world(c, bad).passed


def test_fc12_bans_subtitles_when_none() -> None:
    """禁字幕策略下出现 subtitle 应失败。"""
    c = parse_intent_deterministic("全程不要字幕，约5秒", mode="t2va")
    clean = "integrated_multimodal_description: [Shot 1] Rain on asphalt. overall_soundscape: rain."
    assert fc12_subtitle_policy(c, clean).passed
    dirty = clean + " Burned-in subtitles appear at the bottom."
    assert not fc12_subtitle_policy(c, dirty).passed


def test_fc13_internal_requires_closed_lips() -> None:
    """内心独白必须 VO+闭口。"""
    c = parse_intent_deterministic(
        "女主内心独白「我必须离开」，不要说出口，约5秒",
        mode="t2va",
    )
    bad = 'She shouts: <d>[Chinese] 我必须离开</d>'
    assert not fc13_monologue_closed_lips(c, bad).passed
    good = (
        "She (S1) says in an off-screen voiceover: <d>[Chinese] 我必须离开</d> "
        "while her lips remain completely closed."
    )
    assert fc13_monologue_closed_lips(c, good).passed


def test_spoken_injects_clarity_must() -> None:
    """张嘴对白注入吐字清晰 must。"""
    c = parse_intent_deterministic('小妖说道：「和尚，瞅啥呢？」约6秒', mode="t2va")
    assert "吐字清晰可懂" in c.must_elements
    assert "口型与台词同步" in c.must_elements


def test_evaluate_includes_fc11_13() -> None:
    """evaluate_fidelity 报告含 FC11–FC13。"""
    c = parse_intent_deterministic("全程不要字幕，约4秒", mode="t2va")
    rep = evaluate_fidelity(c, "[Shot 1] Empty street. overall_soundscape: wind.")
    assert "FC11" in rep.checks and "FC12" in rep.checks and "FC13" in rep.checks
