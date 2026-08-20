"""质量校验规则单测：字段结构、对齐句、时间戳、标签、语言标签、画幅残留。"""

from src.verify import (
    check_alignment_line,
    check_canvas_residue,
    check_dialogue_language,
    check_extra_shots,
    check_field_structure,
    check_label_numbers,
    check_label_usage,
    check_music_na,
    check_timestamps,
    intent_allows_multi_shot,
    intent_allows_music_na,
    verify_and_fix,
    verify_prompt,
)

T2VA_OK = """integrated_multimodal_description: [Shot 1] Live-action, cinematic, a cat on a windowsill stretches. The camera pushes in slowly. [Shot 2] At 00:04.000, the camera cuts to a close-up of its tail.

overall_soundscape: Soft room tone with faint street noise.

non_diegetic_music: Gentle acoustic guitar at a slow tempo."""

R2VA_OK = """subject_definitions:
<Subject 1> is the young woman in <Picture 1>, with long dark hair and a blue cardigan.
<Audio 1> is the background music to reuse.

summary:
[reference generation + audio reuse] The target video shows <Subject 1> walking through a sunlit street while <Audio 1> plays.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - identity, clothing, and hair retained.
<Audio 1>: partially_copy - reused as the background score.

detailed_description:
The target video is in realistic style.
[Shot 1] The shot begins with <Subject 1> walking forward in a sunlit street. The music from <Audio 1> plays softly beneath.

overall_soundscape:
Light footsteps and distant traffic.

non_diegetic_music:
The atmospheric music from <Audio 1> reused at low volume."""


def _has_error(issues: list) -> bool:
    """问题列表是否含 error。"""
    return any(i.severity == "error" for i in issues)


def test_verify_t2va_valid() -> None:
    """合法 t2va 提示词不应有任何问题。"""
    assert verify_prompt("t2va", T2VA_OK, duration=6) == []


def test_verify_missing_field() -> None:
    """缺少字段应报 error。"""
    text = T2VA_OK.replace("\noverall_soundscape: Soft room tone with faint street noise.\n", "\n")
    issues = verify_prompt("t2va", text, duration=6)
    assert _has_error(issues)
    assert any(i.code == "field_missing" for i in issues)


def test_verify_field_order() -> None:
    """字段乱序应报 error。"""
    text = T2VA_OK.replace(
        "overall_soundscape: Soft room tone with faint street noise.\n\nnon_diegetic_music:",
        "non_diegetic_music:\n\noverall_soundscape: Soft room tone with faint street noise.\n\nnon_diegetic_music:",
    )
    # 直接构造乱序版本
    text = "overall_soundscape: Soft room tone.\n\nintegrated_multimodal_description: [Shot 1] cat.\n\nnon_diegetic_music: N/A"
    issues = verify_prompt("t2va", text, duration=6)
    assert _has_error(issues)
    assert any(i.code == "field_order" for i in issues)


def test_verify_alignment_line_i2va() -> None:
    """i2va 缺对齐句应报 error；正确时通过。"""
    good = (
        "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced.\n\n" + T2VA_OK
    )
    assert check_alignment_line("i2va", good, 6) == []
    bad = T2VA_OK  # 无对齐句
    assert _has_error(check_alignment_line("i2va", bad, 6))


def test_verify_alignment_duration_fl2va() -> None:
    """fl2va 对齐句 S.SS 与时长不一致应报 error。"""
    good = (
        "How the reference pictures align with the target video — "
        "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
        "Picture 2 (from Shot 1) aligns with the 8.00-second mark of the target video.\n\n" + T2VA_OK
    )
    assert check_alignment_line("fl2va", good, 8) == []
    bad = good.replace("8.00-second", "6.00-second")
    assert _has_error(check_alignment_line("fl2va", bad, 8))


def test_verify_timestamps_not_increasing() -> None:
    """时间戳未严格递增应报 error。"""
    text = (
        "integrated_multimodal_description: [Shot 1] a [Shot 2] At 00:04.000 b "
        "[Shot 3] At 00:02.000 c\n\noverall_soundscape: x\n\nnon_diegetic_music: N/A"
    )
    issues = check_timestamps(text, 6)
    assert _has_error(issues)
    assert any(i.code == "shot_time_not_increasing" for i in issues)


def test_verify_timestamps_over_duration() -> None:
    """时间戳超过时长应报 error。"""
    text = T2VA_OK.replace("At 00:04.000", "At 00:07.000")
    issues = check_timestamps(text, 6)
    assert _has_error(issues)
    assert any(i.code == "shot_time_over_duration" for i in issues)


def test_intent_allows_multi_shot() -> None:
    """只有明确提分镜/切镜才允许多镜头。"""
    assert intent_allows_multi_shot("先中景再切镜到特写") is True
    assert intent_allows_multi_shot("storyboard of three shots, then cut to a close-up") is True
    assert intent_allows_multi_shot("一只橘猫在窗台晒太阳") is False
    assert intent_allows_multi_shot("镜头缓推，人物向前走") is False


def test_check_extra_shots() -> None:
    """未提分镜却写出 Shot 2 应报 error；提了分镜则放行。"""
    issues = check_extra_shots(T2VA_OK, "一只橘猫在窗台晒太阳")
    assert _has_error(issues)
    assert any(i.code == "extra_shot" for i in issues)
    assert check_extra_shots(T2VA_OK, "从中景切镜到尾巴特写") == []
    assert check_extra_shots(T2VA_OK, "") == []
    single = T2VA_OK.replace(
        " [Shot 2] At 00:04.000, the camera cuts to a close-up of its tail.",
        " The camera pushes in to a close-up of its tail.",
    )
    assert check_extra_shots(single, "一只橘猫在窗台晒太阳") == []


def test_intent_allows_music_na() -> None:
    """只有明确要求无配乐 / music=N/A 才允许 non_diegetic_music: N/A。"""
    assert intent_allows_music_na("non_diegetic_music: N/A") is True
    assert intent_allows_music_na("non_diegetic_music为N/A") is True
    assert intent_allows_music_na("不要配乐，只要环境音") is True
    assert intent_allows_music_na("ambience only, no score") is True
    assert intent_allows_music_na("no music, ambient sound only") is True
    assert intent_allows_music_na("一只橘猫在窗台晒太阳") is False
    assert intent_allows_music_na("镜头缓推，人物向前走") is False


def test_check_music_na() -> None:
    """未要求无配乐却写成 N/A 应报 error；明确要求则放行。"""
    na = T2VA_OK.replace(
        "non_diegetic_music: Gentle acoustic guitar at a slow tempo.",
        "non_diegetic_music: N/A",
    )
    issues = check_music_na(na, "一只橘猫在窗台晒太阳")
    assert _has_error(issues)
    assert any(i.code == "music_na_forbidden" for i in issues)
    assert check_music_na(na, "不要配乐") == []
    assert check_music_na(na, "non_diegetic_music: N/A") == []
    assert check_music_na(na, "") == []
    assert check_music_na(T2VA_OK, "一只橘猫在窗台晒太阳") == []


def test_verify_label_numbers() -> None:
    """标签编号超过实际素材数应报 error。"""
    ok = check_label_numbers(T2VA_OK, images=0, videos=0, audios=0)
    assert ok == []
    bad = check_label_numbers(R2VA_OK, images=1, videos=0, audios=0)
    assert _has_error(bad)
    assert any(i.code == "label_overrun" for i in bad)
    good = check_label_numbers(R2VA_OK, images=1, videos=0, audios=1)
    assert good == []


def test_verify_label_usage() -> None:
    """r2va 定义但未引用的标签应报 warning。"""
    assert check_label_usage(R2VA_OK) == []
    text = R2VA_OK.replace("[reference generation + audio reuse] The target video shows <Subject 1> walking through a sunlit street while <Audio 1> plays.", "[reference generation] The target video shows <Subject 1> walking through a sunlit street.")
    text = text.replace("\n<Audio 1>: partially_copy - reused as the background score.", "")
    text = text.replace("The music from <Audio 1> plays softly beneath.", "Background music plays softly beneath.")
    text = text.replace("The atmospheric music from <Audio 1> reused at low volume.", "N/A")
    issues = check_label_usage(text)
    assert any(i.code == "label_unused" for i in issues)


def test_verify_dialogue_language() -> None:
    """<d>[English] 中文内容 应报 error。"""
    bad = "The man says: <d>[English] 你好，今天天气不错。</d>"
    issues = check_dialogue_language(bad)
    assert _has_error(issues)
    assert any(i.code == "dialogue_lang_mismatch" for i in issues)
    good = "The man says: <d>[Chinese] 你好，今天天气不错。</d>"
    assert check_dialogue_language(good) == []
    english_ok = "The man says: <d>[English] Hello, how are you?</d>"
    assert check_dialogue_language(english_ok) == []


def test_verify_canvas_residue() -> None:
    """画幅/分辨率/帧率词残留应报 warning。"""
    assert check_canvas_residue(T2VA_OK) == []
    bad = "resolution 768P 16:9 24fps"
    issues = check_canvas_residue(bad)
    assert any(i.code == "canvas_residue" for i in issues)
    # 连字符复合词（high-resolution）不应误报。
    assert check_canvas_residue("a high-resolution texture") == []


def test_verify_and_fix_fixes_label_overrun() -> None:
    """存在 error 时 verify_and_fix 应调用 chat 修复并重新校验。"""
    calls = {"n": 0}

    def fake_chat(system: str, user: str, *, stage: str = "expand") -> str:  # noqa: ANN001
        calls["n"] += 1
        assert stage == "verify"
        # 修复：去掉越界的 <Audio 1> 定义与引用。
        return R2VA_OK.replace("<Audio 1> is the background music to reuse.", "").replace(
            "\n<Audio 1>: partially_copy - reused as the background score.", ""
        ).replace(
            "[reference generation + audio reuse]", "[reference generation]"
        ).replace(
            " while <Audio 1> plays", ""
        ).replace(
            "The music from <Audio 1> plays softly beneath.", ""
        ).replace(
            "The atmospheric music from <Audio 1> reused at low volume.", "N/A"
        )

    result = verify_and_fix(
        "r2va",
        R2VA_OK,
        duration=5,
        images=1,
        videos=0,
        audios=0,
        chat=fake_chat,
        max_fix_rounds=1,
    )
    assert calls["n"] == 1
    assert result["status"] == "passed"
    assert result["fixed"] is True
    assert "<Audio 1>" not in result["prompt"]


def test_verify_and_fix_no_chat_keeps_errors() -> None:
    """不提供 chat 时，verify_and_fix 不修复、保留原提示词。"""
    bad = R2VA_OK.replace("<Audio 1> is the background music to reuse.", "")
    result = verify_and_fix(
        "r2va",
        bad,
        duration=5,
        images=1,
        videos=0,
        audios=0,
        chat=None,
        max_fix_rounds=1,
    )
    assert result["status"] == "failed"
    assert result["fixed"] is False
    assert result["prompt"] == bad
