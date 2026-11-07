import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from automation.independent_verification import VerificationCheck, VerificationReport, sha256_artifact
from automation.verification_evidence_store import VerificationEvidenceStore
from automation.verification_workflow import VerificationWorkflow
from automation.engineering_acceptance import EngineeringAcceptanceStore, EngineeringAcceptanceError

CHECKS = ("acceptance_criteria", "tests", "security", "architecture", "claims_and_provenance")


def make_report(content=b"source-v1", status="passed"):
    return VerificationReport(
        artifact_sha256=sha256_artifact(content),
        author_agent_id="builder",
        checks=tuple(VerificationCheck(
            name=name, reviewer_agent_id="reviewer-" + name, status=status if name == "tests" else "passed",
            evidence_reference="evidence://" + name, summary="Reviewed " + name,
        ) for name in CHECKS),
        created_at=datetime.now(timezone.utc).isoformat(),
    )


class EngineeringAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.evidence = VerificationEvidenceStore(root / "evidence.sqlite")
        self.workflow = VerificationWorkflow(
            root / "artifacts.sqlite", self.evidence,
            reviewer_authorize=lambda actor, action, artifact: actor == "reviewer" and action == "verification:submit",
        )
        allow = lambda actor, action, plan: actor == "owner" and action.startswith("acceptance:")
        self.store = EngineeringAcceptanceStore(root / "acceptance.sqlite", self.workflow, authorize=allow)
        self.now = datetime.now(timezone.utc).isoformat()
        self.store.register_task(plan_id="plan", subtask_id="subtask", task_id="task",
                                 owner_id="owner", actor_id="owner", created_at=self.now)
        self.artifact = self.workflow.record_artifact(
            plan_id="plan", subtask_id="subtask", task_id="task",
            author_agent_id="builder", content=b"source-v1", created_at=self.now,
        )
        self.store.attach_artifact(plan_id="plan", subtask_id="subtask", task_id="task",
                                   artifact_id=self.artifact.artifact_id, actor_id="owner", created_at=self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def link_report(self, status="passed", content=b"source-v1"):
        saved = self.evidence.record(make_report(content=content, status=status))
        return self.workflow.link_report(
            artifact_id=self.artifact.artifact_id, report_id=saved["report_id"],
            reviewer_actor_id="reviewer", created_at=self.now,
        )

    def refresh(self):
        return self.store.refresh_verification(
            plan_id="plan", subtask_id="subtask", task_id="task",
            actor_id="owner", created_at=self.now,
        )

    def test_execution_artifact_waits_for_verification(self):
        self.assertEqual(self.store.get(plan_id="plan", subtask_id="subtask", task_id="task")["state"],
                         "awaiting_verification")
        self.assertFalse(self.store.can_release_dependents(plan_id="plan", subtask_id="subtask", task_id="task"))

    def test_accepted_only_after_independent_report(self):
        self.link_report()
        result = self.refresh()
        self.assertEqual(result["state"], "accepted")
        self.assertTrue(self.store.can_release_dependents(plan_id="plan", subtask_id="subtask", task_id="task"))

    def test_failed_report_rejects_and_blocks_dependents(self):
        self.link_report(status="failed")
        result = self.refresh()
        self.assertEqual(result["state"], "rejected")
        self.assertFalse(self.store.can_release_dependents(plan_id="plan", subtask_id="subtask", task_id="task"))

    def test_remediation_allows_new_artifact_revision(self):
        self.link_report(status="failed")
        self.refresh()
        self.store.request_remediation(plan_id="plan", subtask_id="subtask", task_id="task",
                                       actor_id="owner", created_at=self.now)
        second = self.workflow.record_artifact(
            plan_id="plan", subtask_id="subtask", task_id="task",
            author_agent_id="builder", content=b"source-v2", created_at=self.now,
        )
        self.store.attach_artifact(plan_id="plan", subtask_id="subtask", task_id="task",
                                   artifact_id=second.artifact_id, actor_id="owner", created_at=self.now)
        self.assertEqual(self.store.get(plan_id="plan", subtask_id="subtask", task_id="task")["state"],
                         "awaiting_verification")
        saved = self.evidence.record(make_report(content=b"source-v2"))
        self.workflow.link_report(artifact_id=second.artifact_id, report_id=saved["report_id"],
                                  reviewer_actor_id="reviewer", created_at=self.now)
        self.assertEqual(self.refresh()["state"], "accepted")

    def test_artifact_cannot_be_attached_to_another_task(self):
        with self.assertRaisesRegex(EngineeringAcceptanceError, "identity_mismatch"):
            self.store.attach_artifact(plan_id="other", subtask_id="subtask", task_id="task",
                                       artifact_id=self.artifact.artifact_id, actor_id="owner", created_at=self.now)

    def test_registration_and_transitions_require_authorization(self):
        with self.assertRaises(PermissionError):
            self.store.register_task(plan_id="p2", subtask_id="s2", task_id="t2",
                                     owner_id="owner", actor_id="intruder", created_at=self.now)


if __name__ == "__main__":
    unittest.main()
