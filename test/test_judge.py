"""十八维裁判：解析、打包、离线打分（不连真实模型）。"""

from __future__ import annotations

import json
from pathlib import Path

from src.eval_dimensions import DIMENSIONS, empty_score_skeleton
from src.judge import (
    aggregate_eval_results,
    build_judge_user,
    evaluate_package,
    load_run_dir,
    package_from_run_record,
    parse_judge_response,
    write_eval_artifacts,
)


def test_parse_judge_response_fills_all_dimensions() -> None:
    """解析结果应覆盖全部 18 维，并裁剪越界分数。"""
    payload = {
        "scores": {
            "d01_instruction_following": 4,
            "d02_visual_quality": 9,
            "d15_audio_generation": "n/a",
        },
        "overall": 4.0,
        "strengths": ["意图保留完整"],
        "weaknesses": ["声音层偏弱"],
        "issue_tags": ["audio_thin"],
        "summary": "整体可用",
    }
    parsed = parse_judge_response(json.dumps(payload, ensure_ascii=False))
    assert len(parsed["scores"]) == len(DIMENSIONS)
    assert parsed["scores"]["d01_instruction_following"] == 4
    assert parsed["scores"]["d02_visual_quality"] == 5
    assert parsed["scores"]["d15_audio_generation"] is None
    assert parsed["scores"]["d03_temporal_stability"] is None
    assert "audio_thin" in parsed["issue_tags"]


def test_parse_judge_response_accepts_fenced_json() -> None:
    """应能从 markdown 代码块中抽出 JSON。"""
    text = '```json\n{"scores":{"d01_instruction_following":3},"summary":"ok"}\n```'
    parsed = parse_judge_response(text)
    assert parsed["scores"]["d01_instruction_following"] == 3
    assert parsed["summary"] == "ok"


def test_build_judge_user_includes_three_artifacts() -> None:
    """USER 包必须含意图、库存、最终提示词三块。"""
    user = build_judge_user(
        {
            "mode": "i2va",
            "intent": "人物向前走",
            "inventory": "INVENTORY: white coat",
            "prompt": "integrated_multimodal_description: [Shot 1] walk",
            "style_skills": ["brand-promo"],
            "style_skill_scores": {"brand-promo": 0.91},
        }
    )
    assert "USER_SHORT_INTENT" in user
    assert "GEMINI_MULTIMODAL_INVENTORY" in user
    assert "FINAL_OPTIMIZED_PROMPT" in user
    assert "人物向前走" in user
    assert "white coat" in user
    assert "0.91" in user


def test_evaluate_package_with_mock_chat(tmp_path: Path) -> None:
    """mock 裁判模型应写出 eval.json / eval.md。"""
    scores = empty_score_skeleton()
    scores["d01_instruction_following"] = 5
    scores["d02_visual_quality"] = 4
    scores["d17_failure_control"] = 5

    def fake_chat(system: str, user: str) -> str:
        assert "d01_instruction_following" in system
        assert "一只橘猫" in user
        return json.dumps(
            {
                "scores": scores,
                "overall": 4.7,
                "strengths": ["清晰"],
                "weaknesses": [],
                "issue_tags": [],
                "summary": "测试通过",
            },
            ensure_ascii=False,
        )

    result = evaluate_package(
        {
            "mode": "t2va",
            "intent": "一只橘猫在窗台晒太阳",
            "inventory": "",
            "prompt": "integrated_multimodal_description: [Shot 1] cat\n\noverall_soundscape: birds\n\nnon_diegetic_music: N/A",
        },
        chat_fn=fake_chat,
    )
    assert result["overall"] == 4.7
    write_eval_artifacts(tmp_path, result)
    assert (tmp_path / "eval.json").is_file()
    assert (tmp_path / "eval.md").is_file()
    data = json.loads((tmp_path / "eval.json").read_text(encoding="utf-8"))
    assert data["scores"]["d01_instruction_following"] == 5


def test_load_run_dir_falls_back_to_txt(tmp_path: Path) -> None:
    """无完整 run.json 时可用 txt 旁路补齐。"""
    (tmp_path / "prompt.txt").write_text("PROMPT BODY\n", encoding="utf-8")
    (tmp_path / "intent.txt").write_text("短意图\n", encoding="utf-8")
    (tmp_path / "inventory.txt").write_text("INV\n", encoding="utf-8")
    rec = load_run_dir(tmp_path)
    pkg = package_from_run_record(rec)
    assert pkg["prompt"].startswith("PROMPT")
    assert pkg["intent"] == "短意图"
    assert pkg["inventory"].startswith("INV")


def test_aggregate_eval_results_means() -> None:
    """批量汇总应计算各维均分与标签计数。"""
    a = {
        "scores": {"d01_instruction_following": 4, "d02_visual_quality": 2},
        "overall": 3.0,
        "issue_tags": ["identity_drift"],
    }
    b = {
        "scores": {"d01_instruction_following": 5, "d02_visual_quality": 4},
        "overall": 4.5,
        "issue_tags": ["identity_drift", "audio_thin"],
    }
    summary = aggregate_eval_results([a, b])
    assert summary["n_cases"] == 2
    assert summary["overall_mean"] == 3.75
    assert summary["dimension_means"]["d01_instruction_following"] == 4.5
    assert summary["issue_tag_counts"]["identity_drift"] == 2
