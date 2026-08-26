"""T0.3 丰富度指标单测：6 项 EN + gold_ir EN5。"""

from __future__ import annotations

from pathlib import Path

from src.contract import IntentContract, parse_intent_deterministic
from src.enrichment import (
    en1_visual_density,
    en2_temporal_coverage,
    en3_sound_layers,
    en4_camera_specificity,
    en5_density_ratio,
    en6_inferred_gain,
    enrichment_median,
    evaluate_enrichment,
)

GOLD_DIR = Path("/kwkj-k8s/zwb/应用/Qwen提示词/母仓/out/gold_ir")

RICH = """integrated_multimodal_description: [Shot 1] At 00:00.000, soft morning light grazes wood texture on a windowsill; gentle slow push-in.
[Shot 2] At 00:03.000, specular reflection on glass, fabric grain, subtle dust motes; camera dollies slowly.

overall_soundscape: Soft room tone ambience, faint footsteps, cloth rustle.

non_diegetic_music: N/A"""


def test_en1_density_increases_with_visual_nouns() -> None:
    """EN1：视觉名词越多分越高。"""
    low = en1_visual_density("A cat sits.")
    high = en1_visual_density(RICH)
    assert high.score >= low.score


def test_en2_temporal_with_timestamps() -> None:
    """EN2：有时间戳时应给出正分。"""
    r = en2_temporal_coverage(RICH, 6.0)
    assert r.score > 0.3


def test_en3_three_layers_with_music_na() -> None:
    """EN3：环境+动作+配乐N/A → 高分。"""
    r = en3_sound_layers(RICH, music_forbidden=True)
    assert r.score >= 0.66


def test_en4_camera_type_amp_speed() -> None:
    """EN4：含运镜类型与幅度速度的镜头得分。"""
    r = en4_camera_specificity(RICH)
    assert r.score > 0.0


def test_en5_ratio_band() -> None:
    """EN5：[0.8,1.2] 得 1.0；无 gold 返回 None。"""
    assert en5_density_ratio("hello world " * 10, None) is None
    a = "word " * 100
    b = "word " * 100
    r = en5_density_ratio(a, b)
    assert r is not None and r.score == 1.0
    thin = en5_density_ratio("word " * 10, "word " * 100)
    assert thin is not None and thin.score < 1.0


def test_en6_inferred_heuristic() -> None:
    """EN6：相对短意图应有增益。"""
    r = en6_inferred_gain("一只猫", RICH)
    assert 0.0 <= r.score <= 1.0


def test_evaluate_mean_of_six() -> None:
    """总分为 6 项均值。"""
    c = parse_intent_deterministic("一只橘猫晒太阳，不要配乐，约6秒")
    report = evaluate_enrichment(c, RICH)
    assert set(report.checks) == {"EN1", "EN2", "EN3", "EN4", "EN5", "EN6"}
    mean = sum(x.score for x in report.checks.values()) / 6.0
    assert abs(report.score - mean) < 1e-9


def test_en5_fill_without_gold() -> None:
    """无 gold 时 EN5 由其他项填充。"""
    c = IntentContract(intent_raw="一只狗", duration_sec=5)
    report = evaluate_enrichment(c, RICH, gold_prompt=None)
    assert not report.used_gold
    assert report.checks["EN5"].detail == "filled_from_others"


def test_en5_on_gold_ir_files() -> None:
    """在母仓 gold_ir 对照上 EN5 可算出（至少若干条）。"""
    if not GOLD_DIR.is_dir():
        return
    files = sorted(GOLD_DIR.glob("*.txt"))
    assert len(files) >= 7
    ok = 0
    for path in files[:19]:
        gold = path.read_text(encoding="utf-8")
        # 用 gold 自身作 local → ratio=1
        r = en5_density_ratio(gold, gold)
        assert r is not None and r.score == 1.0
        ok += 1
    assert ok >= 7


def test_enrichment_median() -> None:
    """集合中位数。"""
    c = IntentContract(intent_raw="x", duration_sec=5)
    reports = [evaluate_enrichment(c, RICH) for _ in range(3)]
    m = enrichment_median(reports)
    assert 0.0 <= m <= 1.0
