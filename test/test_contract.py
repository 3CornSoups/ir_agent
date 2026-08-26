"""T0.1 Intent Contract 单测：schema、确定性抽取、逐字锁、LLM 合并、S3 全量非空。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.contract import (
    DialogueLine,
    IntentContract,
    ShotConstraint,
    assert_verbatim_locks,
    contract_from_llm_payload,
    extract_action_chain,
    extract_duration_sec,
    extract_forbidden,
    extract_onscreen_extended,
    extract_shot_constraint,
    parse_intent,
    parse_intent_deterministic,
)

ROOT = Path(__file__).resolve().parent.parent
S3_PATH = ROOT / "input" / "evalset_v2" / "s3_adversarial.jsonl"

# 方案 §5.3 十二类陷阱样例（各至少覆盖一类断言）
S3_FIXTURES = [
    ("A1", "一只橘猫在窗台晒太阳，禁止切镜，单镜头缓推，约5秒"),
    ("A2", "咖啡倾倒进杯，慢动作，禁止人物入镜，约4秒"),
    ("A3", "雨夜路口红巴士驶过，不要配乐，只要环境声，约6秒"),
    ("A4", "产品旋转展示，画面不得出现任何文字，约5秒"),
    ("A5", "球员停球→转身→射门，动作完整不省略，约6秒"),
    ("A6", "开门→进屋→放包→坐下→打开笔记本，不要跳步，约10秒"),
    ("A7", "她说：『你来了。剑等得够久了。』约5秒"),
    ("A8", "品牌片结尾必须出现中文口号「让每一步都算数」，约6秒"),
    ("A9", "纯手绘速写风格，线条颤抖，不要实拍，约5秒"),
    ("A10", "一只狗"),
    ("A11", "清晨厨房窗边，一只橘猫跳上木质台面，阳光从左侧斜照，猫先抬头嗅气，再缓步走向一碟清水，低头轻舔三下，尾巴轻摆，镜头从中景缓推至特写胡须水珠，全程单镜头禁止切镜，环境声只有时钟与远处鸟鸣，不要配乐，约12秒，不要出现任何字幕或人名"),
    ("A12", "单镜头一镜到底，同时要三个不同场景切换，约8秒"),
]


def test_contract_to_dict_roundtrip() -> None:
    """IntentContract.to_dict 应可 JSON 序列化。"""
    c = IntentContract(
        must_elements=["橘猫"],
        dialogue=[DialogueLine(text="你好", language="Chinese")],
        shot_constraint=ShotConstraint(single_shot=True, max_shots=1),
        intent_raw="x",
        mode="t2va",
    )
    data = c.to_dict()
    assert data["must_elements"] == ["橘猫"]
    assert data["dialogue"][0]["text"] == "你好"
    assert data["shot_constraint"]["single_shot"] is True
    json.dumps(data, ensure_ascii=False)


def test_format_for_prompt_contains_contract_markers() -> None:
    """format_for_prompt 应含 CONTRACT 起止标记。"""
    c = parse_intent_deterministic("一只橘猫，禁止切镜，约5秒")
    block = c.format_for_prompt()
    assert "INTENT CONTRACT" in block
    assert "END CONTRACT" in block
    assert "禁止切镜" in block or c.shot_constraint.single_shot


def test_duration_extract_and_clamp() -> None:
    """时长抽取并夹到 4–15。"""
    assert extract_duration_sec("约5秒") == 5.0
    assert extract_duration_sec("大概20秒") == 15.0
    assert extract_duration_sec("约2秒") == 4.0
    assert extract_duration_sec("一只狗") == 5.0


def test_action_chain_arrow() -> None:
    """箭头动作链应按序抽取。"""
    assert extract_action_chain("球员停球→转身→射门") == ["停球", "转身", "射门"]
    assert extract_action_chain("开门→进屋→放包→坐下→打开笔记本") == [
        "开门",
        "进屋",
        "放包",
        "坐下",
        "打开笔记本",
    ]
    assert extract_action_chain("一只猫晒太阳") == []


def test_shot_constraint_single() -> None:
    """禁止切镜 / 单镜头 → single_shot。"""
    shot = extract_shot_constraint("禁止切镜，单镜头缓推")
    assert shot.single_shot is True


def test_short_clip_defaults_to_single_shot() -> None:
    """约6秒短片无显式多镜时，默认 single_shot（对齐官方）。"""
    c = parse_intent_deterministic("雨夜霓虹路口，红色巴士驶过，约6秒，电影感")
    assert c.shot_constraint.single_shot is True
    assert c.shot_constraint.max_shots == 1


def test_chase_intent_not_forced_single_shot() -> None:
    """追逐/飞身等多拍意图不应被短片默认压成单镜。"""
    c = parse_intent_deterministic("3D武侠动画：屋顶追逐，壮汉挥杖砸瓦，戴斗笠的身影飞身踢开兵器，日落金光，约6秒")
    assert c.shot_constraint.single_shot is False


def test_sequential_beats_not_forced_single_shot() -> None:
    """先/再/最后分步意图不应被短片默认压成单镜。"""
    c = parse_intent_deterministic("先给产品特写，再展示开箱，最后字幕 CTA，禁止加人脸，约6秒")
    assert c.shot_constraint.single_shot is False


def test_visual_locks_enter_must_elements() -> None:
    """浅景深/电影感等成像属性进入 must_elements。"""
    c = parse_intent_deterministic("雨夜路口，红色巴士，电影感 live-action，浅景深，约6秒")
    joined = " ".join(c.must_elements)
    assert "浅景深" in joined
    assert "电影感" in joined or "live-action" in joined.lower()


def test_near_foreground_blur_lock() -> None:
    """近景虚影掠过镜头应进入视觉锁 must。"""
    c = parse_intent_deterministic(
        "窄巷雨夜，浅景深，近景行人虚影掠过镜头，胶片颗粒，约6秒，电影感"
    )
    joined = " ".join(c.must_elements)
    assert "虚影掠过" in joined or "近景行人虚影" in joined
    assert "胶片颗粒" in joined


def test_forbidden_negations() -> None:
    """否定约束应被抽出。"""
    hits = extract_forbidden("禁止人物入镜，不要配乐")
    assert any("人物" in h or "人脸" in h for h in hits)
    assert any("配乐" in h for h in hits)


def test_dialogue_verbatim_a7() -> None:
    """A7：中文台词原文必须逐字出现在 contract.dialogue。"""
    intent = "她说：『你来了。剑等得够久了。』约5秒"
    c = parse_intent_deterministic(intent)
    texts = [d.text for d in c.dialogue]
    assert "你来了。剑等得够久了。" in texts
    assert_verbatim_locks(c, intent)


def test_onscreen_slogan_a8() -> None:
    """A8：口号应进入 onscreen_text 且逐字。"""
    intent = "品牌片结尾必须出现中文口号「让每一步都算数」，约6秒"
    c = parse_intent_deterministic(intent)
    assert "让每一步都算数" in c.onscreen_text
    assert_verbatim_locks(c, intent)


def test_explicit_style_a9() -> None:
    """A9：纯手绘 + 不要实拍。"""
    intent = "纯手绘速写风格，线条颤抖，不要实拍，约5秒"
    c = parse_intent_deterministic(intent)
    assert c.explicit_style is not None
    assert "手绘" in c.explicit_style
    assert any("实拍" in n for n in c.explicit_negatives)


def test_ambiguity_a12() -> None:
    """A12：单镜头与多场景冲突应记入 ambiguities。"""
    intent = "单镜头一镜到底，同时要三个不同场景切换，约8秒"
    c = parse_intent_deterministic(intent)
    assert c.shot_constraint.single_shot is True
    assert c.ambiguities, "应记录自相矛盾"


def test_minimal_intent_a10_nonempty() -> None:
    """A10：极简意图仍应 is_nonempty。"""
    c = parse_intent_deterministic("一只狗")
    assert c.is_nonempty()
    assert c.intent_raw == "一只狗"


def test_llm_cannot_rewrite_dialogue() -> None:
    """LLM 若改写对白正文，无效命中后回退确定性原文。"""
    intent = "她说：『你来了。剑等得够久了。』"
    payload = {
        "must_elements": ["她"],
        "forbidden": [],
        "quote_labels": [
            {
                "text": "You have arrived. The sword waited long.",
                "role": "spoken",
                "speaker": "她",
                "delivery": "spoken",
            }
        ],
        "dialogue": [{"text": "You have arrived. The sword waited long.", "language": "English"}],
        "onscreen_text": [],
        "shot_constraint": {"single_shot": False, "max_shots": None},
        "duration_sec": 5,
        "action_chain": [],
        "explicit_style": None,
        "explicit_negatives": [],
        "ambiguities": [],
    }
    c = contract_from_llm_payload(payload, intent=intent, mode="t2va")
    assert [d.text for d in c.dialogue] == ["你来了。剑等得够久了。"]
    assert_verbatim_locks(c, intent)

def test_llm_quote_labels_ignore_style_and_keep_speech() -> None:
    """LLM quote_labels：风格 ignore、台词 spoken、口号 onscreen。"""
    intent = "用「赛博朋克」风格，她说「加油」，结尾口号「让每一步都算数」"
    payload = {
        "must_elements": ["她"],
        "forbidden": [],
        "quote_labels": [
            {"text": "赛博朋克", "role": "ignore"},
            {"text": "加油", "role": "spoken", "speaker": "她", "delivery": "spoken"},
            {"text": "让每一步都算数", "role": "onscreen"},
        ],
        "dialogue": [{"text": "加油", "speaker": "她", "delivery": "spoken"}],
        "onscreen_text": ["让每一步都算数"],
        "shot_constraint": {"single_shot": True, "max_shots": 1},
        "duration_sec": 5,
        "action_chain": [],
        "explicit_style": "赛博朋克",
        "explicit_negatives": [],
        "ambiguities": [],
    }
    c = contract_from_llm_payload(payload, intent=intent, mode="t2va")
    assert [d.text for d in c.dialogue] == ["加油"]
    assert "赛博朋克" not in [d.text for d in c.dialogue]
    assert "让每一步都算数" in c.onscreen_text
    assert "赛博朋克" not in c.onscreen_text
    assert_verbatim_locks(c, intent)


def test_bare_quotes_not_dialogue_deterministic() -> None:
    """确定性路径：无说话线索的裸引号不当台词。"""
    c = parse_intent_deterministic("用「赛博朋克」风格拍一只猫，产品名叫「知麦」，约5秒")
    assert c.dialogue == []
    assert "赛博朋克" not in c.onscreen_text



def test_parse_intent_with_mock_chat() -> None:
    """parse_intent 应调用 chat(stage=parse_intent) 并合并结果。"""
    calls: list[dict] = []

    def fake_chat(system: str, user: str, *, stage: str = "") -> str:
        calls.append({"stage": stage, "system": system[:40], "user": user[:80]})
        return json.dumps(
            {
                "must_elements": ["橘猫", "窗台"],
                "forbidden": ["禁止切镜"],
                "dialogue": [],
                "onscreen_text": [],
                "shot_constraint": {"single_shot": True, "max_shots": 1},
                "duration_sec": 5,
                "action_chain": [],
                "explicit_style": None,
                "explicit_negatives": [],
                "ambiguities": [],
            },
            ensure_ascii=False,
        )

    intent = "一只橘猫在窗台晒太阳，禁止切镜，约5秒"
    c = parse_intent(intent, mode="t2va", chat=fake_chat, use_llm=True)
    assert calls and calls[0]["stage"] == "parse_intent"
    assert "橘猫" in c.must_elements
    assert c.shot_constraint.single_shot is True


def test_parse_intent_llm_fallback_on_bad_json() -> None:
    """LLM 返回坏 JSON 时应回退确定性抽取。"""

    def bad_chat(system: str, user: str, *, stage: str = "") -> str:
        return "not-json-at-all"

    c = parse_intent("球员停球→转身→射门，约6秒", chat=bad_chat, use_llm=True)
    assert c.action_chain == ["停球", "转身", "射门"]


def test_no_llm_flag() -> None:
    """use_llm=False 不调用 chat。"""

    def boom(*_a, **_k) -> str:
        raise RuntimeError("不应调用")

    c = parse_intent("不要配乐，约4秒", chat=boom, use_llm=False)
    assert any("配乐" in f for f in c.forbidden)


def test_assert_verbatim_raises_on_rewrite() -> None:
    """人为改写对白时 assert_verbatim_locks 应失败。"""
    c = IntentContract(
        dialogue=[DialogueLine(text="被改写的台词")],
        intent_raw="她说：『原文』",
    )
    with pytest.raises(AssertionError):
        assert_verbatim_locks(c, c.intent_raw)


def test_s3_fixture_patterns_nonempty_and_verbatim() -> None:
    """十二类对抗样例：contract 非空，对白/屏上字逐字。"""
    for code, intent in S3_FIXTURES:
        c = parse_intent_deterministic(intent, mode="t2va")
        assert c.is_nonempty(), f"{code} 应抽出非空 contract: {intent!r}"
        assert_verbatim_locks(c, intent)


def test_s3_jsonl_all_nonempty_if_present() -> None:
    """若 S3 文件存在：全部 48 条抽出非空 contract，逐字锁成立。"""
    if not S3_PATH.is_file():
        pytest.skip("s3_adversarial.jsonl 尚未生成")
    rows = [json.loads(line) for line in S3_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 48, f"期望 48 条，实际 {len(rows)}"
    for row in rows:
        intent = row["intent"]
        mode = row.get("mode") or "t2va"
        c = parse_intent_deterministic(intent, mode=mode)
        assert c.is_nonempty(), f"{row.get('id')} 空 contract"
        assert_verbatim_locks(c, intent)
        # 对白/屏上字若 manifest 声明了 expected，则必须命中
        for line in row.get("expected_dialogue") or []:
            assert any(d.text == line for d in c.dialogue), f"{row.get('id')} 缺对白 {line!r}"
        for line in row.get("expected_onscreen") or []:
            assert line in c.onscreen_text, f"{row.get('id')} 缺屏上字 {line!r}"


def test_extract_onscreen_extended_no_false_dialogue() -> None:
    """口号进 onscreen 后不应再当作对白。"""
    intent = "结尾口号「让每一步都算数」，她说：『加油』"
    onscreen = extract_onscreen_extended(intent)
    assert "让每一步都算数" in onscreen
    c = parse_intent_deterministic(intent)
    assert "让每一步都算数" not in [d.text for d in c.dialogue]
    assert any(d.text == "加油" for d in c.dialogue)
