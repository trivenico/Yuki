import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from core.task_evidence import EvidenceAttestation, TaskEvidenceStore
from integrations.github_actions_artifacts import (
    GitHubActionsArtifactVerifier,
    GitHubArtifactVerificationError,
)


class GitHubActionsArtifactVerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.allowed = {
            "github-actions-webhook:acme/yuki:12345",
            "github-actions-artifact:acme/yuki:12345",
            "github-actions-test-report:acme/yuki:12345",
        }
        self.store = TaskEvidenceStore(
            str(Path(self.tmp.name) / "evidence.sqlite3"),
            attestation_verifier=lambda item: item.verifier_id in self.allowed,
        )
        self.report = b"test_module ... ok\n\n----------------------------------------------------------------------\nRan 3 tests in 0.012s\n\nOK\n"
        stream = io.BytesIO()
        with ZipFile(stream, "w") as zipped:
            zipped.writestr("yuki-test-results.log", self.report)
        self.archive = stream.getvalue()
        self.run = {
            "id": 77, "workflow_id": 12345, "conclusion": "success",
            "head_sha": "a" * 40, "head_branch": "main",
            "repository_full_name": "acme/yuki",
        }
        self.artifact = {
            "id": 88, "name": "yuki-test-report-77", "expired": False,
            "workflow_run_id": 77, "digest": "sha256:" + hashlib.sha256(self.archive).hexdigest(),
            "size_in_bytes": len(self.archive),
        }
        self.verifier = self.build()

    def build(self, *, run=None, artifact=None, archive=None, binding=None):
        return GitHubActionsArtifactVerifier(
            evidence_store=self.store,
            allowed_repositories={"acme/yuki"},
            allowed_workflow_ids={12345},
            owner_for_repository={"acme/yuki": "owner-1"},
            task_binding_resolver=lambda owner, task: binding if (owner, task) == ("owner-1", "task-1") else None,
            workflow_run_lookup=lambda repo, run_id: run if (repo, run_id) == ("acme/yuki", 77) else None,
            artifact_lookup=lambda repo, run_id, name: artifact if name == "yuki-test-report-77" else None,
            artifact_download=lambda repo, artifact_id: archive if artifact_id == 88 else None,
        )

    def add_ci_evidence(self):
        self.store.record_verified(EvidenceAttestation(
            owner_id="owner-1", task_id="task-1", task_version=3,
            kind="ci_workflow", reference="https://github.com/acme/yuki/actions/runs/77",
            digest_sha256="c" * 64,
            verifier_id="github-actions-webhook:acme/yuki:12345",
            verified_at="2026-11-23T10:00:00+00:00",
        ))

    def test_records_artifact_and_successful_test_report(self):
        self.add_ci_evidence()
        result = self.verifier.verify(repository="acme/yuki", run_id=77, task_id="task-1", task_version=3)
        self.assertEqual(result.test_count, 3)
        records = self.store.list_verified(owner_id="owner-1", task_id="task-1", task_version=3)
        self.assertEqual({item.kind for item in records}, {"ci_workflow", "artifact", "test_report"})
        self.assertEqual(result.artifact_digest_sha256, hashlib.sha256(self.archive).hexdigest())
        self.assertEqual(result.report_digest_sha256, hashlib.sha256(self.report).hexdigest())

    def test_requires_prior_matching_ci_webhook_evidence(self):
        with self.assertRaises(GitHubArtifactVerificationError):
            self.verifier.verify(repository="acme/yuki", run_id=77, task_id="task-1", task_version=3)

    def test_rejects_artifact_digest_mismatch(self):
        self.add_ci_evidence()
        bad = dict(self.artifact, digest="sha256:" + "0" * 64)
        with self.assertRaises(GitHubArtifactVerificationError):
            self.build(artifact=bad).verify(repository="acme/yuki", run_id=77, task_id="task-1", task_version=3)

    def test_rejects_failed_run_or_stale_source_binding(self):
        self.add_ci_evidence()
        failed = dict(self.run, conclusion="failure")
        with self.assertRaises(GitHubArtifactVerificationError):
            self.build(run=failed).verify(repository="acme/yuki", run_id=77, task_id="task-1", task_version=3)
        stale = dict(self.run, head_sha="b" * 40)
        with self.assertRaises(GitHubArtifactVerificationError):
            self.build(run=stale).verify(repository="acme/yuki", run_id=77, task_id="task-1", task_version=3)

    def test_rejects_missing_or_mismatched_artifact(self):
        self.add_ci_evidence()
        expired = dict(self.artifact, expired=True)
        with self.assertRaises(GitHubArtifactVerificationError):
            self.build(artifact=expired).verify(repository="acme/yuki", run_id=77, task_id="task-1", task_version=3)

    def test_rejects_report_without_success_summary(self):
        self.add_ci_evidence()
        stream = io.BytesIO()
        with ZipFile(stream, "w") as zipped:
            zipped.writestr("yuki-test-results.log", b"Ran 3 tests\nFAILED (failures=1)\n")
        archive = stream.getvalue()
        artifact = dict(self.artifact, digest="sha256:" + hashlib.sha256(archive).hexdigest(), size_in_bytes=len(archive))
        with self.assertRaises(GitHubArtifactVerificationError):
            self.build(artifact=artifact, archive=archive).verify(
                repository="acme/yuki", run_id=77, task_id="task-1", task_version=3,
            )

    def test_rejects_unallowlisted_repository_and_task_version(self):
        with self.assertRaises(GitHubArtifactVerificationError):
            self.verifier.verify(repository="other/repo", run_id=77, task_id="task-1", task_version=3)
        with self.assertRaises(GitHubArtifactVerificationError):
            self.verifier.verify(repository="acme/yuki", run_id=77, task_id="task-1", task_version=4)


if __name__ == "__main__":
    unittest.main()
