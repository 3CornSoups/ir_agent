from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import pipeline
from src.config import SKILL_SETS, load_skills
from src.media import _build_video_part
from src.pipeline import infer_duration, strip_canvas
from src.postprocess import (
    clean_final_prompt,
    ensure_alignment_prefix,
    verify_local,
)
from src.prompts import compose_format_system, compose_plan_system


class CanvasTests(unittest.TestCase):
    def test_numeric_production_parameters_are_removed(self):
        cleaned = strip_canvas("scene, aspect ratio: 16:9, then action at 24 fps.")
        self.assertNotIn("16:9", cleaned)
        self.assertNotIn("24 fps", cleaned)

    def test_benign_phrases_kept(self):
        text = "Audio is reused 1:1; this is an FPS game; the resolution of the standoff arrives."
        self.assertEqual(strip_canvas(text).strip(), text)

    def test_dialogue_ratio_not_stripped(self):
        raw = "He says: <d>[Chinese] 咱们四比三分，4:3 就这样定了</d>"
        cleaned = strip_canvas(raw)
        self.assertIn("4:3", cleaned)
        self.assertIn("<d>", cleaned)

    def test_onscreen_quoted_4k_kept(self):
        raw = 'A sign reading "4K HDR 影院" glows.'
        cleaned = strip_canvas(raw)
        self.assertIn("4K", cleaned)
        self.assertIn("影院", cleaned)

    def test_bare_2k_in_prose_kept(self):
        # 无制作参数关键词时不再误删正文里的 2K
        raw = "The 2K monitor on the desk shows static."
        cleaned = strip_canvas(raw)
        self.assertIn("2K", cleaned)


class AlignmentTests(unittest.TestCase):
    def test_i2va_prefix_injected(self):
        out = ensure_alignment_prefix(
            "i2va",
            "integrated_multimodal_description: [Shot 1] hello",
            5,
        )
        self.assertTrue(out.startswith("For the target video, at 0.00 seconds"))

    def test_near_miss_alignment_not_duplicated(self):
        body = (
            "How the reference pictures align with target video — "
            "<Picture 1> (from [Shot 1]) aligns with the 5.00-second mark.\n\n"
            "integrated_multimodal_description: [Shot 1] x\n\n"
            "overall_soundscape: a\n\n"
            "non_diegetic_music: N/A"
        )
        out = ensure_alignment_prefix("l2va", body, 5)
        self.assertEqual(
            sum(1 for line in out.splitlines() if "align" in line.lower()),
            1,
        )

    def test_shot_index_from_body_not_bad_alignment(self):
        body = (
            "How the reference pictures align with the target video — "
            "<Picture 1> (from [Shot 7]) aligns with the 5.00-second mark "
            "of the target video.\n\n"
            "integrated_multimodal_description: [Shot 1] only one shot\n\n"
            "overall_soundscape: a\n\n"
            "non_diegetic_music: N/A"
        )
        out = ensure_alignment_prefix("l2va", body, 5)
        self.assertIn("(from [Shot 1])", out.splitlines()[0])
        self.assertNotIn("[Shot 7]", out.splitlines()[0])


class DurationTests(unittest.TestCase):
    def test_english_seconds(self):
        self.assertEqual(infer_duration("an 8 second clip"), 8)
        self.assertEqual(infer_duration("a 12-second clip"), 12)

    def test_chinese_digit_words(self):
        self.assertEqual(infer_duration("大概十秒的空镜"), 10)


class ModePrefixTests(unittest.TestCase):
    def test_clean_final_prompt_strips_mode_prefix(self):
        raw = (
            "MODE=t2va\n"
            "integrated_multimodal_description: [Shot 1] x\n\n"
            "overall_soundscape: a\n\n"
            "non_diegetic_music: N/A"
        )
        cleaned = clean_final_prompt(raw, "t2va", 5)
        self.assertFalse(cleaned.upper().startswith("MODE="))

    def test_inline_mode_prefix_stripped(self):
        cleaned = clean_final_prompt(
            "MODE=t2va integrated_multimodal_description: [Shot 1] x\n\n"
            "overall_soundscape: a\n\nnon_diegetic_music: N/A",
            "t2va",
            5,
        )
        self.assertFalse(cleaned.upper().startswith("MODE="))


class SkillAssemblyTests(unittest.TestCase):
    def test_format_t2va_contains_speaker_and_not_retention(self):
        sys_text = compose_format_system("r2_format", "t2va")
        self.assertIn("(S1)", sys_text)
        self.assertIn("MM:SS.mmm", sys_text)
        self.assertIn("H3 syntax skills (binding)", sys_text)
        self.assertNotIn("fully_preserved", sys_text)
        self.assertIn("Official example", sys_text)

    def test_format_r2va_contains_retention(self):
        sys_text = compose_format_system("r3_format_r2va", "r2va")
        self.assertIn("fully_preserved", sys_text)
        self.assertIn("subject_definitions", sys_text)

    def test_plan_system_has_cast_skill(self):
        sys_text = compose_plan_system("r1_keyinfo", "t2va")
        self.assertIn("[CAST]", sys_text)
        self.assertIn("SHOT PLAN", sys_text)

    def test_skill_sets_files_exist(self):
        for name, stems in SKILL_SETS.items():
            text = load_skills(name)
            self.assertTrue(text.strip(), name)
            for stem in stems:
                self.assertIn(stem.split("_", 1)[0], text or stem)


class VerifyLocalTests(unittest.TestCase):
    def test_dialogue_forbidden_violated(self):
        prompt = (
            "integrated_multimodal_description: [Shot 1] A woman (S1) says: "
            "<d>[Chinese] 你好</d>\n\n"
            "overall_soundscape: wind\n\n"
            "non_diegetic_music: N/A"
        )
        result = verify_local(
            prompt,
            mode="t2va",
            duration=5,
            upstream="[CAST]\ndialogue=forbidden\n",
        )
        codes = {i["code"] for i in result["issues"]}
        self.assertIn("dialogue_forbidden_violated", codes)

    def test_speaker_id_missing(self):
        prompt = (
            "integrated_multimodal_description: [Shot 1] Someone says: "
            "<d>[English] Hello</d>\n\n"
            "overall_soundscape: wind\n\n"
            "non_diegetic_music: N/A"
        )
        result = verify_local(
            prompt,
            mode="t2va",
            duration=5,
            upstream='[CAST]\n(S1) = man | role: lead\n(S1): "Hello"\n',
        )
        codes = {i["code"] for i in result["issues"]}
        self.assertIn("speaker_id_missing", codes)

    def test_shot_time_not_increasing(self):
        prompt = (
            "integrated_multimodal_description: [Shot 1] open. "
            "[Shot 2] At 00:05.000, the camera cuts to a close-up. "
            "[Shot 3] At 00:04.000, the camera cuts to a wide shot.\n\n"
            "overall_soundscape: wind\n\n"
            "non_diegetic_music: N/A"
        )
        result = verify_local(prompt, mode="t2va", duration=8, upstream="")
        codes = {i["code"] for i in result["issues"]}
        self.assertIn("shot_time_not_increasing", codes)


class SlimPipelineTests(unittest.TestCase):
    def test_t2va_uses_two_rounds_only(self):
        calls = []

        def fake_chat(system, user, *, stage="expand", model=None):
            calls.append((stage, model))
            if stage == "expand":
                return (
                    "[USER CONSTRAINTS]\n- Duration: 10\n"
                    "[CAST]\n(S1) = baker | role: lead\n(S1): \"First batch\"\n"
                    "[SHOT PLAN]\nShot 1 | cut — | bakery | speakers: (S1)\n"
                    "[POLISHED INTENT]\nA quiet park scene."
                )
            if stage == "format":
                return (
                    "integrated_multimodal_description: [Shot 1] The baker (S1) says: "
                    "<d>[English] First batch</d>\n\n"
                    "overall_soundscape: ambience\n\n"
                    "non_diegetic_music: N/A"
                )
            raise AssertionError(stage)

        with patch.object(pipeline, "chat", side_effect=fake_chat), patch.object(
            pipeline, "_text_model_name", return_value="text-lite"
        ):
            result = pipeline.enhance(
                "t2va",
                "测试意图",
                duration=10,
                skill_router="hybrid",
                mechanism_router="hybrid",
                enable_verify=False,
            )

        self.assertEqual(result["duration"], 10)
        self.assertEqual([stage for stage, _ in calls], ["expand", "format"])
        self.assertEqual([model for _, model in calls], ["text-lite", "text-lite"])
        self.assertEqual(len(result["rounds"]), 2)
        self.assertIn("integrated_multimodal_description", result["prompt"])
        self.assertEqual(result["style_skills"], [])
        self.assertEqual(result["mechanisms"], [])
        self.assertIn("status", result["verify"])


class OpenAIVideoPartTests(unittest.TestCase):
    """openai 协议下视频应统一为 image_url，避免 video_url 被兼容网关丢弃。"""

    def test_openai_http_video_uses_image_url(self):
        """http 视频在 openai 协议下伪装为 image_url。"""
        part = _build_video_part(
            "https://example.com/clip.mp4",
            protocol="openai",
            media_cfg={},
        )
        self.assertEqual(part["type"], "image_url")
        self.assertEqual(part["image_url"]["url"], "https://example.com/clip.mp4")

    def test_openai_local_video_uses_image_url_data_uri(self):
        """本地视频在 openai 协议下转为 data URI 并放入 image_url。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tiny.mp4"
            path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
            part = _build_video_part(
                str(path),
                protocol="openai",
                media_cfg={
                    "download_timeout_sec": 5,
                    "max_download_bytes": 64 * 1024 * 1024,
                    "max_image_bytes": 30 * 1024 * 1024,
                },
            )
        self.assertIsNotNone(part)
        self.assertEqual(part["type"], "image_url")
        url = part["image_url"]["url"]
        self.assertTrue(url.startswith("data:video/"), url[:40])
        self.assertNotEqual(part.get("type"), "video_url")

    def test_native_local_video_keeps_video_url(self):
        """native 协议仍使用 video_url，供后续转 inlineData。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tiny.mp4"
            path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
            part = _build_video_part(
                str(path),
                protocol="native",
                media_cfg={
                    "download_timeout_sec": 5,
                    "max_download_bytes": 64 * 1024 * 1024,
                    "max_image_bytes": 30 * 1024 * 1024,
                },
            )
        self.assertEqual(part["type"], "video_url")
        self.assertTrue(part["video_url"]["url"].startswith("data:video/"))


if __name__ == "__main__":
    unittest.main()
