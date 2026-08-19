"""T8 Creative DNA 机制路由：关键词、JSON 解析、强制指定。"""

from src.mechanism_router import (
    known_mechanism_ids,
    mechanism_block_for_user,
    parse_classify_response,
    select_mechanisms,
    writing_blocks_for_user,
)


def test_t8_catalog_not_empty() -> None:
    """同步后应包含 T8 v1.1.8 的机制目录。"""
    ids = known_mechanism_ids()
    assert len(ids) >= 100
    assert "structure-product-proof-launch" in ids
    assert "sensory-seal-location-swap-resumption" in ids


def test_keyword_route_picks_product_proof() -> None:
    """产品证据递进意图应命中 structure-product-proof-launch。"""
    sel = select_mechanisms(
        "产品广告｜功能证据递进，先给终态再逐层证明功能，最后 CTA",
        router="keyword",
    )
    assert "structure-product-proof-launch" in sel.ids


def test_forced_mechanism() -> None:
    """--mechanism 强制加载时不依赖关键词。"""
    sel = select_mechanisms(
        "一只橘猫在窗台晒太阳",
        forced=["verify-recurring-identity-board"],
        router="off",
    )
    assert sel.ids == ["verify-recurring-identity-board"]
    block = mechanism_block_for_user(sel)
    assert block is not None
    assert "verify-recurring-identity-board" in block
    assert "Mandatory structural anchors" in block


def test_llm_route_mechanisms_json() -> None:
    """前置模型应返回 mechanisms 键。"""

    def classify(*_a, **_k) -> str:  # noqa: ANN001
        return '{"mechanisms":["build-earned-arrival-journey"]}'

    sel = select_mechanisms(
        "人物从困境一路走到目标地点",
        router="llm",
        classify=classify,
    )
    assert sel.ids == ["build-earned-arrival-journey"]


def test_parse_classify_accepts_legacy_skills_key() -> None:
    """兼容误返回 skills 键的 JSON。"""
    assert parse_classify_response('{"skills":["stage-two-turn-pause-reaction"]}') == [
        "stage-two-turn-pause-reaction"
    ]


def test_writing_blocks_merge_style_and_mechanism() -> None:
    """题材 style 与机制块应合并进扩写 USER。"""
    mech = select_mechanisms(
        "ignored",
        forced=["structure-product-proof-launch"],
        router="off",
    )
    merged = writing_blocks_for_user("STYLE-BLOCK", mechanism_block_for_user(mech))
    assert merged is not None
    assert "STYLE-BLOCK" in merged
    assert "structure-product-proof-launch" in merged
