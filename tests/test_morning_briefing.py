import unittest
from datetime import datetime, timedelta, timezone

from companion.briefing import render_morning_briefing


class MorningBriefingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 27, 8, 30, tzinfo=timezone.utc)

    def render(self, reminders=None, **overrides):
        values = {
            "now": self.now,
            "workspace": "C:/Projects/Yuki",
            "provider": "ollama",
            "skills_count": 4,
            "reminders": reminders or [],
            "voice_enabled": False,
        }
        values.update(overrides)
        return render_morning_briefing(**values)

    def test_briefing_reports_core_status_without_claiming_live_provider_readiness(self):
        output = self.render()
        self.assertIn("Yuki morning briefing", output)
        self.assertIn("Workspace: C:/Projects/Yuki", output)
        self.assertIn("Provider: ollama (configured; live readiness is not inferred)", output)
        self.assertIn("Skills available: 4", output)
        self.assertIn("Voice: disabled in settings", output)
        self.assertIn("read-only snapshot", output)

    def test_briefing_separates_due_and_upcoming_reminders(self):
        reminders = [
            {"id": "a", "text": "Pay invoice", "due_at": (self.now - timedelta(minutes=1)).isoformat(), "delivered": False},
            {"id": "b", "text": "Team meeting", "due_at": (self.now + timedelta(hours=2)).isoformat(), "delivered": False},
            {"id": "c", "text": "Already delivered", "due_at": (self.now - timedelta(hours=2)).isoformat(), "delivered": True},
            {"id": "d", "text": "Malformed date", "due_at": "not-a-date", "delivered": False},
        ]
        output = self.render(reminders=reminders)
        self.assertIn("Reminders: 3 pending, 1 due/overdue", output)
        self.assertIn("- Due: Pay invoice", output)
        self.assertIn("- Upcoming", output)
        self.assertIn("Team meeting", output)
        self.assertNotIn("Already delivered", output)

    def test_briefing_skips_naive_and_malformed_due_dates(self):
        output = self.render(reminders=[
            {"text": "Naive date", "due_at": "2026-10-27T07:00:00", "delivered": False},
            {"text": "Bad date", "due_at": "bad", "delivered": False},
        ])
        self.assertIn("Reminders: 2 pending, 0 due/overdue", output)

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.render(now=datetime(2026, 10, 27, 8, 30))
        with self.assertRaisesRegex(ValueError, "non-negative"):
            self.render(skills_count=-1)
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self.render(workspace=" ")


if __name__ == "__main__":
    unittest.main()
