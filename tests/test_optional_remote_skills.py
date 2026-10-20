"""Offline tests for opt-in remote skills; provider calls are always mocked."""

import os
import unittest
from unittest.mock import Mock, patch

from skills.image_skill import ImageGenerationSkill
from skills.webcam_skill import WebcamSkill


class OptionalRemoteSkillTests(unittest.TestCase):
    def test_image_skill_rejects_empty_and_oversized_prompts(self):
        skill = ImageGenerationSkill()
        self.assertFalse(skill.run("").ok)
        self.assertFalse(skill.run("x" * 2001).ok)

    def test_image_skill_never_calls_provider_without_api_key(self):
        skill = ImageGenerationSkill()
        with patch.dict(os.environ, {}, clear=True):
            result = skill.run("a tree")
        self.assertFalse(result.ok)
        self.assertIn("OPENAI_API_KEY", result.message)

    def test_image_skill_reports_missing_optional_sdk(self):
        skill = ImageGenerationSkill()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"openai": None}):
                result = skill.run("a tree")
        self.assertFalse(result.ok)
        self.assertIn("requirements-image.txt", result.message)

    def test_image_skill_success_is_explicitly_remote(self):
        data = Mock()
        data.url = "https://images.example.test/result.png"
        client = Mock()
        client.images.generate.return_value.data = [data]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"openai": Mock(OpenAI=Mock(return_value=client))}):
                result = ImageGenerationSkill().run("a tree")
        self.assertTrue(result.ok)
        self.assertTrue(result.data["remote_processing"])
        self.assertEqual(result.data["url"], data.url)
        client.images.generate.assert_called_once()

    def test_image_skill_rejects_non_https_provider_url(self):
        data = Mock()
        data.url = "http://images.example.test/result.png"
        client = Mock()
        client.images.generate.return_value.data = [data]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"openai": Mock(OpenAI=Mock(return_value=client))}):
                result = ImageGenerationSkill().run("a tree")
        self.assertFalse(result.ok)
        self.assertIn("HTTPS", result.message)

    def test_image_skill_sanitizes_provider_exceptions(self):
        client = Mock()
        client.images.generate.side_effect = RuntimeError("sensitive provider detail")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"openai": Mock(OpenAI=Mock(return_value=client))}):
                result = ImageGenerationSkill().run("a tree")
        self.assertFalse(result.ok)
        self.assertNotIn("sensitive provider detail", result.message)
        self.assertIn("RuntimeError", result.message)

    def test_webcam_requires_explicit_supported_action(self):
        result = WebcamSkill().run("record continuously")
        self.assertFalse(result.ok)
        self.assertIn("camera index 0", result.message)

    def test_webcam_does_not_capture_without_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            result = WebcamSkill().run("capture")
        self.assertFalse(result.ok)
        self.assertIn("no frame was captured", result.message)

    def test_webcam_reports_missing_optional_dependencies(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"cv2": None}):
                result = WebcamSkill().run("capture")
        self.assertFalse(result.ok)
        self.assertIn("requirements-vision.txt", result.message)


if __name__ == "__main__":
    unittest.main()
