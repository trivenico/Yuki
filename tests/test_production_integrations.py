import tempfile
import unittest
from datetime import date
from pathlib import Path

from automation.browser import SafeBrowser, BrowserAutomationError
from core.mcp_client import MCPHTTPClient, MCPError
from companion.proactive import summarize_habit_streaks
from skills.registry import SkillRegistry
from skills.youtube_skill import YouTubeSkill
from voice.tts import LocalTTS


class ProductionIntegrationTests(unittest.TestCase):
    def test_optional_skills_are_registered_without_starting_external_services(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = SkillRegistry.defaults(Path(directory), web_allowed_hosts=[])
            for name in ("web_deep_read", "knowledge", "health_log", "security_remediation",
                         "browser_read", "youtube_transcript"):
                self.assertIn(name, registry.names())

    def test_browser_denies_navigation_without_allowlist(self):
        with self.assertRaises(BrowserAutomationError):
            SafeBrowser(allowed_hosts=[]).read_page("https://example.com")

    def test_browser_rejects_non_https_before_launch(self):
        with self.assertRaises(BrowserAutomationError):
            SafeBrowser(allowed_hosts=["example.com"]).read_page("http://example.com")

    def test_youtube_skill_rejects_invalid_video_identifier(self):
        result = YouTubeSkill().execute("not-a-youtube-video")
        self.assertFalse(result.ok)
        self.assertIn("11-character", result.message)

    def test_mcp_requires_https_and_allowlisted_host(self):
        with self.assertRaises(MCPError):
            MCPHTTPClient("http://example.com/mcp", allowed_hosts=["example.com"])
        with self.assertRaises(MCPError):
            MCPHTTPClient("https://example.com/mcp", allowed_hosts=[])

    def test_mcp_tool_call_fails_closed_without_approval(self):
        client = MCPHTTPClient.__new__(MCPHTTPClient)
        client.allowed_tools = frozenset({"calendar.create"})
        client.approver = None
        with self.assertRaises(MCPError):
            client.call_tool("calendar.create", {"title": "test"})

    def test_tts_chunk_stream_stops_after_interruption(self):
        class FakeTTS(LocalTTS):
            def __init__(self):
                super().__init__(enabled=True, backend="pyttsx3")
                self.spoken = []
            def speak(self, text):
                self.spoken.append(text)
                if text == "first":
                    self.stop()
        tts = FakeTTS()
        result = tts.speak_stream(["first", "second"])
        self.assertEqual(tts.spoken, ["first"])
        self.assertTrue(result["interrupted"])

    def test_habit_streaks_use_explicit_logs_only(self):
        entries = [
            {"type": "habit", "detail": "Read", "occurred_at": "2026-10-15T10:00:00+00:00"},
            {"type": "habit", "detail": "Read", "occurred_at": "2026-10-16T10:00:00+00:00"},
            {"type": "habit", "detail": "Read", "occurred_at": "2026-10-17T10:00:00+00:00"},
            {"type": "mood", "detail": "happy", "occurred_at": "2026-10-17T10:00:00+00:00"},
        ]
        result = summarize_habit_streaks(entries, today=date(2026, 10, 17))
        self.assertEqual(result[0]["current_streak_days"], 3)
        self.assertEqual(result[0]["source"], "explicit_user_logs")


if __name__ == "__main__":
    unittest.main()
