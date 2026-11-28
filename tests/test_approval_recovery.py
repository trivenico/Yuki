"""Tests for read-only approval persistence recovery diagnostics."""
import unittest

from core.approval_recovery import scan_approval_recovery


class FakePendingStore:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def list_recovery_metadata(self, *, limit):
        self.calls += 1
        return list(self.rows)[:limit]


class FakeGrantStore:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def list_recovery_metadata(self, *, limit):
        self.calls += 1
        return list(self.rows)[:limit]


class FakeAuditLog:
    def __init__(self, events, *, valid=True, unresolved=None):
        self.events = events
        self.valid = valid
        self.unresolved = unresolved or []
        self.calls = []

    def verify_chain(self):
        self.calls.append("verify")
        return self.valid

    def list_events(self, *, limit):
        self.calls.append("events")
        return list(self.events)[:limit]

    def list_unresolved_executions(self, *, limit):
        self.calls.append("unresolved")
        return list(self.unresolved)[:limit]


class ApprovalRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.pending = FakePendingStore([])
        self.grants = FakeGrantStore([])
        self.audit = FakeAuditLog([])

    def test_reports_orphan_pending_expired_grant_and_unresolved_dispatch(self):
        self.pending.rows = [{
            "pending_id_hash": "p" * 64, "skill_id": "notes.write",
            "expires_at": "2026-01-01T00:00:00+00:00", "expired": True,
        }]
        self.grants.rows = [{
            "approval_id_hash": "g" * 64, "action_digest": "a" * 64,
            "expires_at": "2026-01-01T00:00:00+00:00", "expired": True,
        }]
        self.audit.events = [{
            "event_id": 7, "event_type": "requested",
            "pending_id_hash": "q" * 64,
        }]
        self.audit.unresolved = [{
            "started_event_id": 9, "pending_id_hash": "u" * 64,
            "skill_id": "notes.delete", "started_at": "2026-01-01T00:00:00+00:00",
        }]

        report = scan_approval_recovery(
            pending_store=self.pending, audit_log=self.audit,
            grant_store=self.grants,
        )
        kinds = {finding["kind"] for finding in report.findings}
        self.assertEqual(kinds, {
            "orphan_pending_snapshot",
            "expired_one_shot_grant",
            "execution_outcome_requires_reconciliation",
        })
        self.assertTrue(report.audit_chain_valid)
        self.assertTrue(report.complete)
        self.assertEqual(self.pending.calls, 1)
        self.assertEqual(self.grants.calls, 1)
        self.assertEqual(self.audit.calls, ["verify", "events", "unresolved"])

    def test_reports_approved_without_dispatch_marker(self):
        self.audit.events = [
            {"event_id": 3, "event_type": "approved", "pending_id_hash": "p" * 64},
            {"event_id": 2, "event_type": "requested", "pending_id_hash": "p" * 64},
        ]
        report = scan_approval_recovery(
            pending_store=self.pending, audit_log=self.audit,
            grant_store=self.grants,
        )
        self.assertIn(
            "approved_without_dispatch_marker",
            {finding["kind"] for finding in report.findings},
        )

    def test_invalid_audit_chain_fails_closed_and_skips_correlation(self):
        self.audit.valid = False
        self.audit.events = [{"event_type": "requested", "pending_id_hash": "p" * 64}]
        report = scan_approval_recovery(
            pending_store=self.pending, audit_log=self.audit,
            grant_store=self.grants,
        )
        self.assertFalse(report.audit_chain_valid)
        self.assertFalse(report.complete)
        self.assertEqual(report.findings, ())
        self.assertEqual(self.audit.calls, ["verify"])

    def test_limit_is_validated_and_truncation_is_reported(self):
        self.pending.rows = [{"pending_id_hash": "p" * 64}] * 2
        with self.assertRaises(ValueError):
            scan_approval_recovery(
                pending_store=self.pending, audit_log=self.audit,
                grant_store=self.grants, limit=0,
            )
        report = scan_approval_recovery(
            pending_store=self.pending, audit_log=self.audit,
            grant_store=self.grants, limit=1,
        )
        self.assertFalse(report.complete)
        self.assertTrue(any("Pending snapshot listing reached" in item for item in report.warnings))

    def test_scanner_requires_read_only_diagnostic_interfaces(self):
        with self.assertRaisesRegex(TypeError, "read-only recovery diagnostics"):
            scan_approval_recovery(
                pending_store=object(), audit_log=self.audit,
                grant_store=self.grants,
            )


if __name__ == "__main__":
    unittest.main()
