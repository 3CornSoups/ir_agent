"""风格 skill 路由：关键词、JSON 解析、强制指定。"""

from src.skill_router import (
    known_skill_ids,
    parse_classify_response,
    select_style_skills,
    style_block_for_user,
)


def test_catalog_covers_official_genre_skills() -> None:
    """目录应覆盖官方八个题材 skill 的可移植写法。"""
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
    } <= ids


def test_keyword_route_picks_brand_promo() -> None:
    """品牌宣传意图应靠关键词命中 brand-promo，不请求模型。"""
    calls = {"n": 0}

    def classify(*_a, **_k):  # noqa: ANN001
        calls["n"] += 1
        raise AssertionError("关键词已命中时 hybrid 不应再问模型")

    sel = select_style_skills(
        "给产品拍一支品牌宣传片，结尾 CTA 和 logo lockup",
        router="hybrid",
        classify=classify,
    )
    assert sel.ids == ["brand-promo"]
    assert sel.source == "keyword"
    assert calls["n"] == 0


def test_keyword_route_prefers_minimalist_product_ad() -> None:
    """极简产品广告应命中更具体的 minimalist-product-ad。"""
    sel = select_style_skills(
        "Apple 风极简产品广告，留白和 negative space",
        router="keyword",
    )
    assert "minimalist-product-ad" in sel.ids


def test_generic_intent_loads_nothing_on_keyword() -> None:
    """普通生活场景不应加载题材 skill。"""
    sel = select_style_skills("一只橘猫在窗台晒太阳", router="keyword")
    assert sel.ids == []
    assert style_block_for_user(sel) is None


def test_llm_route_when_keywords_miss() -> None:
    """关键词未命中时，hybrid 采用前置模型返回的 id。"""

    def classify(system: str, user: str, *, stage: str = "route") -> str:
        assert stage == "route"
        assert "co-op-game-intro" in user
        assert "Short intent:" in user
        return '{"skills":["co-op-game-intro"]}'

    sel = select_style_skills(
        "两个角色在标题画面里点 Continue 准备出发",
        router="hybrid",
        classify=classify,
    )
    assert sel.ids == ["co-op-game-intro"]
    assert sel.source == "llm"


def test_llm_route_generic_scene() -> None:
    """无关键词时前置模型返回空列表。"""
    def classify(*_a, **_k) -> str:  # noqa: ANN001
        return '{"skills":[]}'

    sel = select_style_skills("雨夜路口一辆红巴士驶过", router="hybrid", classify=classify)
    assert sel.ids == []
    assert sel.source == "none"


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
