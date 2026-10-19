import tempfile
import unittest
from pathlib import Path

from core.earning_companion import EarningCompanion


class EarningCompanionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "earning.sqlite3"
        self.app = EarningCompanion(self.db_path)

    def test_add_goal_and_summary(self):
        self.app.add_goal("First freelance client", 500, "usd")
        summary = self.app.summary("USD")
        self.assertEqual(summary.active_goals, 1)
        self.assertEqual(summary.recorded_revenue, 0)

    def test_track_opportunity_and_update_status(self):
        opportunity_id = self.app.add_opportunity(
            "Portfolio website", "freelancing", "Publish two case studies", 250
        )
        self.assertEqual(self.app.summary().open_opportunities, 1)
        self.app.update_opportunity(opportunity_id, status="won")
        self.assertEqual(self.app.summary().open_opportunities, 0)

    def test_record_revenue_is_separate_from_estimates(self):
        self.app.add_opportunity("Possible project", "freelancing", "Send proposal", 1000)
        self.app.record_revenue(125.50, "Client invoice", "2026-10-18", note="Paid")
        summary = self.app.summary()
        self.assertEqual(summary.recorded_revenue, 125.50)

    def test_currency_totals_are_not_mixed(self):
        self.app.record_revenue(100, "Sale", "2026-10-18", "USD")
        self.app.record_revenue(100, "Sale", "2026-10-18", "EUR")
        self.assertEqual(self.app.summary("USD").recorded_revenue, 100)
        self.assertEqual(self.app.summary("EUR").recorded_revenue, 100)

    def test_invalid_goal_amount_rejected(self):
        with self.assertRaises(ValueError):
            self.app.add_goal("Goal", -1)

    def test_invalid_status_rejected(self):
        with self.assertRaises(ValueError):
            self.app.add_opportunity("Idea", "online", "Research", status="magic")

    def test_invalid_revenue_date_rejected(self):
        with self.assertRaises(ValueError):
            self.app.record_revenue(10, "Sale", "yesterday")

    def test_database_persists_between_instances(self):
        self.app.add_goal("Persistent goal", 200)
        reopened = EarningCompanion(self.db_path)
        self.assertEqual(reopened.summary().active_goals, 1)


if __name__ == "__main__":
    unittest.main()
