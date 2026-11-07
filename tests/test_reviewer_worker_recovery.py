"""Tests for lease-aware reviewer worker recovery."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from automation.reviewer_ledger import ReviewerLedgerConflict
from automation.reviewer_worker_recovery import ReviewerWorkerRecoverySupervisor


class ReviewerWorkerRecoveryTests(unittest.TestCase):
    def test_expired_worker_and_expired_task_lease_recover_atomically(self):
        registry = Mock()
        registry.expire_stale.return_value = 1
        registry.list_workers.return_value = (
            Mock(state="expired"),
        )
        ledger = Mock()
        ledger.list_expired_in_progress.return_value = ({
            "task_id": "task-a", "reviewer_agent_id": "reviewer-a",
            "version": 4, "artifact_sha256": "a" * 64,
        },)
        ledger.recover_in_progress.return_value = {
            "task_id": "task-a", "state": "assigned", "version": 6,
        }
        supervisor = ReviewerWorkerRecoverySupervisor(
            registry, ledger, actor_id="recovery-controller",
            worker_ids=("reviewer-a",),
            replacement_resolver=lambda task, previous: "reviewer-b",
        )
        report = supervisor.run_once()
        self.assertEqual(report.recovered_tasks, 1)
        ledger.recover_in_progress.assert_called_once_with(
            actor_id="recovery-controller", task_id="task-a",
            expected_version=4, replacement_reviewer_agent_id="reviewer-b",
            reason="reviewer_worker_lease_expired",
        )

    def test_live_instance_prevents_recovery_when_historical_instance_expired(self):
        registry = Mock()
        registry.expire_stale.return_value = 0
        registry.list_workers.return_value = (
            Mock(state="expired"), Mock(state="running"),
        )
        ledger = Mock()
        supervisor = ReviewerWorkerRecoverySupervisor(
            registry, ledger, actor_id="recovery-controller",
            worker_ids=("reviewer-a",),
            replacement_resolver=lambda *_: "reviewer-b",
        )
        report = supervisor.run_once()
        self.assertEqual(report.expired_worker_ids, ())
        self.assertEqual(report.recovered_tasks, 0)
        ledger.list_expired_in_progress.assert_not_called()

    def test_no_expired_worker_does_not_query_or_recover_tasks(self):
        registry = Mock()
        registry.expire_stale.return_value = 0
        registry.list_workers.return_value = (Mock(state="running"),)
        ledger = Mock()
        supervisor = ReviewerWorkerRecoverySupervisor(
            registry, ledger, actor_id="recovery-controller",
            worker_ids=("reviewer-a",),
            replacement_resolver=lambda *_: "reviewer-b",
        )
        report = supervisor.run_once()
        self.assertEqual(report.recovered_tasks, 0)
        ledger.list_expired_in_progress.assert_not_called()

    def test_concurrent_task_change_is_reported_as_deferred(self):
        registry = Mock()
        registry.expire_stale.return_value = 1
        registry.list_workers.return_value = (Mock(state="expired"),)
        ledger = Mock()
        ledger.list_expired_in_progress.return_value = ({
            "task_id": "task-a", "reviewer_agent_id": "reviewer-a",
            "version": 4,
        },)
        ledger.recover_in_progress.side_effect = ReviewerLedgerConflict("stale_version")
        supervisor = ReviewerWorkerRecoverySupervisor(
            registry, ledger, actor_id="recovery-controller",
            worker_ids=("reviewer-a",),
            replacement_resolver=lambda *_: "reviewer-b",
        )
        report = supervisor.run_once()
        self.assertEqual(report.recovered_tasks, 0)
        self.assertEqual(report.deferred_tasks, 1)
        self.assertEqual(report.outcomes[0].reason_code,
                         "task_or_lease_changed_during_recovery")

    def test_missing_replacement_defers_without_mutating_task(self):
        registry = Mock()
        registry.expire_stale.return_value = 1
        registry.list_workers.return_value = (Mock(state="expired"),)
        ledger = Mock()
        ledger.list_expired_in_progress.return_value = ({
            "task_id": "task-a", "reviewer_agent_id": "reviewer-a",
            "version": 4,
        },)
        supervisor = ReviewerWorkerRecoverySupervisor(
            registry, ledger, actor_id="recovery-controller",
            worker_ids=("reviewer-a",),
            replacement_resolver=lambda *_: None,
        )
        report = supervisor.run_once()
        self.assertEqual(report.deferred_tasks, 1)
        ledger.recover_in_progress.assert_not_called()


if __name__ == "__main__":
    unittest.main()
