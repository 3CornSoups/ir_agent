"""风格 skill 路由：LLM 量化打分、强制指定。"""

import json

from src.skill_router import (
    known_skill_ids,
    parse_classify_response,
    parse_classify_scores,
    pick_top1_above_threshold,
    select_style_skills,
    style_block_for_user,
)


def test_catalog_covers_official_genre_skills() -> None:
    """目录应覆盖官方八个题材 skill，以及已接入的街访写法。"""
    ids = set(known_skill_ids())
    assert {
        "brand-promo",
        "minimalist-product-ad",
        "3d-animation",
        "papercraft-stop-motion",
        "paper-collage",
        "music-video-subtitle",
        "co-op-game-intro",
        "handdrawn-live",
        "direct-street-interview",
        "stage-startle-to-truce",
    } <= ids


def test_community_catalog_entries_have_upstream() -> None:
    """社区 skill 必须带 upstream，便于溯源。"""
    from src.skill_router import load_catalog

    load_catalog.cache_clear()
    community = [s for s in load_catalog() if s.origin == "community"]
    assert community
    for item in community:
        assert item.upstream.startswith("http"), item.id


def test_llm_route_loads_direct_street_interview() -> None:
    """街访意图经打分可选中 direct-street-interview，并加载 community overlay。"""
    from src.skill_router import load_catalog

    load_catalog.cache_clear()

    def classify(*_a, **_k) -> str:  # noqa: ANN001
        return json.dumps({"scores": {"direct-street-interview": 0.92, "brand-promo": 0.1}})

    sel = select_style_skills(
        "竖屏街头采访，边走边聊，第一人称跟拍",
        router="hybrid",
        classify=classify,
    )
    assert sel.ids == ["direct-street-interview"]
    assert sel.source == "llm"
    detail = sel.detail_records()
    assert detail == [
        {
            "id": "direct-street-interview",
            "origin": "community",
            "upstream": (
                "https://github.com/T8mars/minimax-h3-prompt-skill-T8/"
                "tree/main/skills/direct-street-interview-video"
            ),
        }
    ]
    block = style_block_for_user(sel)
    assert block is not None
    assert "style skill: direct-street-interview" in block
    assert "community" in block
    assert "walk-and-talk" in block.lower() or "street" in block.lower()


def test_llm_route_loads_stage_startle_to_truce() -> None:
    """遭遇意图经打分可选中 stage-startle-to-truce。"""
    from src.skill_router import load_catalog

    load_catalog.cache_clear()

    def classify(*_a, **_k) -> str:  # noqa: ANN001
        return json.dumps({"scores": {"stage-startle-to-truce": 0.88, "3d-animation": 0.2}})

    sel = select_style_skills(
        "驾驶舱舷窗对视，先惊愕后休战手势的近距遭遇",
        router="hybrid",
        classify=classify,
    )
    assert sel.ids == ["stage-startle-to-truce"]
    assert sel.source == "llm"
    detail = {d["id"]: d for d in sel.detail_records()}
    assert detail["stage-startle-to-truce"]["origin"] == "community"
    assert "stage-startle-to-truce-encounter" in detail["stage-startle-to-truce"]["upstream"]
    block = style_block_for_user(sel)
    assert block is not None
    assert "spatial reversal" in block.lower() or "truce" in block.lower()


def test_hybrid_always_calls_llm() -> None:
    """hybrid 始终走打分，不再因字面触发词跳过模型。"""
    calls = {"n": 0}

    def classify(*_a, **_k) -> str:  # noqa: ANN001
        calls["n"] += 1
        return json.dumps({"scores": {"brand-promo": 0.91, "3d-animation": 0.05}})

    sel = select_style_skills(
        "给产品拍一支品牌宣传片，结尾 CTA 和 logo lockup",
        router="hybrid",
        classify=classify,
    )
    assert calls["n"] == 1
    assert sel.ids == ["brand-promo"]
    assert sel.source == "llm"


def test_keyword_alias_equals_llm() -> None:
    """历史 keyword 模式别名为 llm。"""

    def classify(*_a, **_k) -> str:  # noqa: ANN001
        return json.dumps({"scores": {"minimalist-product-ad": 0.85}})

    sel = select_style_skills(
        "Apple 风极简产品广告",
        router="keyword",
        classify=classify,
    )
    assert sel.ids == ["minimalist-product-ad"]
    assert sel.source == "llm"


def test_llm_route_picks_legacy_skills_list() -> None:
    """兼容旧 skills 列表（视为 1.0）。"""

    def classify(system: str, user: str, *, stage: str = "route") -> str:
        assert stage == "route"
        assert "co-op-game-intro" in user
        assert "Short intent:" in user
        assert "Score threshold" in user
        return '{"skills":["co-op-game-intro"]}'

    sel = select_style_skills(
        "两个角色在标题画面里点 Continue 准备出发",
        router="hybrid",
        classify=classify,
    )
    assert sel.ids == ["co-op-game-intro"]
    assert sel.source == "llm"
    assert sel.llm_top1_score == 1.0
    meta = sel.llm_route_meta()
    assert meta is not None
    assert meta["accepted"] is True


def test_llm_score_picks_top1_above_threshold() -> None:
    """打分模式取最高分且达阈值的 skill。"""

    def classify(*_a, **_k) -> str:  # noqa: ANN001
        return json.dumps(
            {
                "scores": {
                    "brand-promo": 0.2,
                    "co-op-game-intro": 0.81,
                    "3d-animation": 0.4,
                }
            }
        )

    sel = select_style_skills(
        "两个角色在标题画面里点 Continue",
        router="hybrid",
        classify=classify,
    )
    assert sel.ids == ["co-op-game-intro"]
    assert sel.source == "llm"
    assert sel.llm_top1_score == 0.81


def test_llm_score_below_threshold_selects_none() -> None:
    """top1 未达阈值时不选风格 skill。"""

    def classify(*_a, **_k) -> str:  # noqa: ANN001
        return json.dumps(
            {
                "scores": {
                    "brand-promo": 0.3,
                    "co-op-game-intro": 0.55,
                    "3d-animation": 0.1,
                }
            }
        )

    sel = select_style_skills(
        "雨夜路口一辆红巴士驶过",
        router="hybrid",
        classify=classify,
    )
    assert sel.ids == []
    assert sel.source == "none"
    assert sel.llm_top1_score == 0.55
    meta = sel.llm_route_meta()
    assert meta is not None
    assert meta["accepted"] is False
    assert meta["threshold"] == 0.6


def test_llm_route_generic_scene() -> None:
    """泛场景低分/空分时不选。"""

    def classify(*_a, **_k) -> str:  # noqa: ANN001
        return '{"scores":{}}'

    sel = select_style_skills("雨夜路口一辆红巴士驶过", router="hybrid", classify=classify)
    assert sel.ids == []
    assert sel.source == "none"


def test_without_classify_selects_none() -> None:
    """未提供 classify 时 hybrid/llm 无法打分，不选。"""
    sel = select_style_skills("一只橘猫在窗台晒太阳", router="hybrid", classify=None)
    assert sel.ids == []
    assert style_block_for_user(sel) is None


def test_forced_skill_merges_in_off_mode() -> None:
    """skill_router=off 时只保留强制 id。"""
    sel = select_style_skills(
        "一只橘猫在窗台晒太阳",
        forced=["3d-animation", "not-a-skill"],
        router="off",
    )
    assert sel.ids == ["3d-animation"]
    assert sel.source == "forced"
    block = style_block_for_user(sel)
    assert block is not None
    assert "squash-and-stretch" in block


def test_parse_classify_response_ignores_unknown_and_fenced_json() -> None:
    """非法 JSON 或未知 id 应丢弃。"""
    assert parse_classify_response("not json") == []
    assert parse_classify_response('```json\n{"skills":["brand-promo","nope"]}\n```') == [
        "brand-promo"
    ]
    assert parse_classify_response('{"skills":["brand_promo"]}') == ["brand-promo"]


def test_parse_classify_scores_and_threshold() -> None:
    """分数解析与 top1 阈值筛选。"""
    scores = parse_classify_scores(
        '{"scores":{"brand-promo":0.2,"co-op-game-intro":0.9,"nope":1.0}}'
    )
    assert scores == {"brand-promo": 0.2, "co-op-game-intro": 0.9}
    ids, top = pick_top1_above_threshold(scores, 0.6)
    assert ids == ["co-op-game-intro"] and top == 0.9
    ids2, top2 = pick_top1_above_threshold({"brand-promo": 0.4}, 0.6)
    assert ids2 == [] and top2 == 0.4
