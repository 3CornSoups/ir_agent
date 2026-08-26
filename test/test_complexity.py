"""复杂度预算：由 Intent Contract 推导目标词数。"""

from src.complexity import complexity_word_budget, estimate_shot_count, format_complexity_budget_block
from src.contract import IntentContract, ShotConstraint, parse_intent_deterministic


def test_single_shot_budget_is_compact() -> None:
    """单镜头短意图的目标词数应偏紧，避免灌水。"""
    c = IntentContract(
        must_elements=["橘猫"],
        action_chain=["晒太阳"],
        shot_constraint=ShotConstraint(single_shot=True),
        duration_sec=5.0,
        intent_raw="一只橘猫晒太阳，禁止切镜，约5秒",
    )
    assert estimate_shot_count(c) == 1
    lo, hi = complexity_word_budget(c)
    assert 80 <= lo < hi <= 400


def test_filmic_locks_raise_single_shot_floor() -> None:
    """电影感视觉锁应抬高单镜词数下限（对齐官方 cinematic 密度）。"""
    plain = IntentContract(
        must_elements=["橘猫"],
        action_chain=["晒太阳"],
        shot_constraint=ShotConstraint(single_shot=True),
        duration_sec=6.0,
        intent_raw="一只橘猫晒太阳，约6秒",
    )
    filmic = IntentContract(
        must_elements=["霓虹", "电影感", "浅景深", "live-action"],
        action_chain=["巴士穿过"],
        shot_constraint=ShotConstraint(single_shot=True),
        duration_sec=6.0,
        intent_raw="雨夜霓虹，电影感 live-action，浅景深，约6秒",
    )
    lo_p, _ = complexity_word_budget(plain)
    lo_f, hi_f = complexity_word_budget(filmic)
    assert lo_f >= 280
    assert lo_f >= lo_p
    block = format_complexity_budget_block(filmic)
    assert "FILMIC SINGLE-SHOT" in block
    assert f"{lo_f}-{hi_f}" in block


def test_densify_user_mentions_budget_floor() -> None:
    """欠词 densify USER 应含预算下限与虚影提醒。"""
    from src.complexity import densify_user_block

    text = densify_user_block(
        "short note",
        contract_block="must_elements: [\"近景行人虚影掠过镜头\"]",
        complexity_block="COMPLEXITY BUDGET",
        lo=280,
        hi=400,
    )
    assert "280" in text
    assert "虚影" in text or "near-foreground" in text


def test_multi_beat_budget_is_larger() -> None:
    """多必须元素 + 长动作链应抬高词数上限。"""
    c = parse_intent_deterministic(
        "球员停球→转身→射门→庆祝，禁止切镜，约8秒"
    )
    c.must_elements = ["球员", "足球", "球门"]
    c.shot_constraint = ShotConstraint(single_shot=True)
    lo, hi = complexity_word_budget(c)
    lo2, hi2 = complexity_word_budget(
        IntentContract(
            must_elements=["猫"],
            action_chain=["坐"],
            shot_constraint=ShotConstraint(single_shot=True),
            duration_sec=4.0,
        )
    )
    assert hi >= hi2
    block = format_complexity_budget_block(c)
    assert "COMPLEXITY BUDGET" in block
    assert f"{lo}-{hi}" in block
