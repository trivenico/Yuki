import tempfile
import unittest
from pathlib import Path

from automation.browser import SafeBrowser, BrowserAutomationError
from skills.registry import SkillRegistry
from skills.youtube_skill import YouTubeSkill


class ProductionIntegrationTests(unittest.TestCase):
    def test_optional_skills_are_registered_without_starting_external_services(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = SkillRegistry.defaults(Path(directory), web_allowed_hosts=[])
            for name in ("web_deep_read", "knowledge", "health_log", "security_remediation",
                         "browser_read", "youtube_transcript"):
                self.assertIn(name, registry.names())

    def test_browser_denies_navigation_without_allowlist(self):
        browser = SafeBrowser(allowed_hosts=[])
        with self.assertRaises(BrowserAutomationError):
            browser.read_page("https://example.com")

    def test_browser_rejects_non_https_before_launch(self):
        browser = SafeBrowser(allowed_hosts=["example.com"])
        with self.assertRaises(BrowserAutomationError):
            browser.read_page("http://example.com")

    def test_youtube_skill_rejects_invalid_video_identifier(self):
        result = YouTubeSkill().execute("not-a-youtube-video")
        self.assertFalse(result.ok)
        self.assertIn("11-character", result.message)


if __name__ == "__main__":
    unittest.main()
