import tempfile
import unittest
from pathlib import Path

from core.income_opportunities import SQLiteIncomeOpportunityStore


class IncomeOpportunityStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "income.sqlite3")
        self.store = SQLiteIncomeOpportunityStore(self.db, max_per_owner=2)

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, owner="owner-a", **overrides):
        values = {
            "platform_id": "sample-market",
            "title": "Remote design project",
            "category": "freelance",
            "source_url": "https://example.com/opportunity/123",
            "description": "A research lead, not a verified job.",
            "evidence_level": "unverified",
            "currency": "GBP",
            "estimated_net_minor": 25000,
            "startup_cost_minor": 1200,
            "estimated_hours": 5,
            "fit_score": 80,
            "demand_score": 70,
            "competition_score": 40,
        }
        values.update(overrides)
        return self.store.create(owner, **values)

    def test_create_and_restart_persistence(self):
        item = self.create()
        restarted = SQLiteIncomeOpportunityStore(self.db)
        loaded = restarted.get("owner-a", item.opportunity_id)
        self.assertEqual(loaded, item)
        self.assertEqual(loaded.status, "discovered")
        self.assertEqual(loaded.estimated_net_minor, 25000)

    def test_owner_isolation_for_get_list_transition_and_delete(self):
        item = self.create()
        self.assertIsNone(self.store.get("owner-b", item.opportunity_id))
        self.assertEqual(self.store.list("owner-b"), [])
        self.assertIsNone(self.store.transition("owner-b", item.opportunity_id, new_status="researching"))
        self.assertFalse(self.store.delete("owner-b", item.opportunity_id))
        self.assertEqual(self.store.get("owner-a", item.opportunity_id).status, "discovered")

    def test_status_transitions_are_explicit_and_terminal_states_cannot_reopen(self):
        item = self.create()
        item = self.store.transition("owner-a", item.opportunity_id, new_status="researching")
        item = self.store.transition("owner-a", item.opportunity_id, new_status="validated")
        item = self.store.transition("owner-a", item.opportunity_id, new_status="ready")
        item = self.store.transition("owner-a", item.opportunity_id, new_status="active")
        item = self.store.transition("owner-a", item.opportunity_id, new_status="completed")
        self.assertEqual(item.status, "completed")
        with self.assertRaisesRegex(ValueError, "invalid_opportunity_transition"):
            self.store.transition("owner-a", item.opportunity_id, new_status="active")

    def test_invalid_transition_and_unknown_status_rejected(self):
        item = self.create()
        with self.assertRaisesRegex(ValueError, "invalid_opportunity_transition"):
            self.store.transition("owner-a", item.opportunity_id, new_status="active")
        with self.assertRaisesRegex(ValueError, "invalid_status"):
            self.store.list("owner-a", status="paid")

    def test_research_estimates_are_optional_and_not_realized_revenue(self):
        item = self.create(estimated_net_minor=None, estimated_hours=None)
        self.assertIsNone(item.estimated_net_minor)
        self.assertIsNone(item.estimated_hours)
        self.assertFalse(hasattr(item, "realized_revenue_minor"))

    def test_input_validation_rejects_unsafe_urls_and_invalid_money(self):
        for url in (
            "http://example.com/job",
            "https://user:pass@example.com/job",
            "file:///etc/passwd",
            "https://example.com:444/job",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.create(source_url=url)
        with self.assertRaisesRegex(ValueError, "invalid_estimated_net_minor"):
            self.create(estimated_net_minor=True)
        with self.assertRaisesRegex(ValueError, "invalid_startup_cost_minor"):
            self.create(startup_cost_minor=-1)
        with self.assertRaisesRegex(ValueError, "invalid_fit_score"):
            self.create(fit_score=101)

    def test_capacity_is_per_owner_and_bounded(self):
        self.create("owner-a")
        self.create("owner-a", title="Second opportunity")
        self.create("owner-b")
        with self.assertRaisesRegex(ValueError, "owner_opportunity_capacity_reached"):
            self.create("owner-a", title="Third opportunity")

    def test_list_filters_and_limit(self):
        a = self.create(platform_id="sample-market")
        self.create(platform_id="another-market", title="Second opportunity")
        self.assertEqual(len(self.store.list("owner-a", platform_id="sample-market")), 1)
        self.assertEqual(len(self.store.list("owner-a", status="discovered", limit=2)), 2)
        with self.assertRaisesRegex(ValueError, "invalid_limit"):
            self.store.list("owner-a", limit=501)

    def test_delete_is_owner_scoped_and_idempotent(self):
        item = self.create()
        self.assertTrue(self.store.delete("owner-a", item.opportunity_id))
        self.assertFalse(self.store.delete("owner-a", item.opportunity_id))


if __name__ == "__main__":
    unittest.main()
