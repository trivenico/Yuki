import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from automation.independent_verification import VerificationCheck, VerificationReport
from automation.local_verification_executor import LocalVerificationExecutor, VerificationCommand
from automation.verification_evidence_store import VerificationEvidenceStore, VerificationStoreError


class ExecutionReceiptEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.artifact = b"immutable source artifact"
        self.digest = hashlib.sha256(self.artifact).hexdigest()
        self.executor = LocalVerificationExecutor(
            self.root, self.root / "executions.sqlite3",
            {
                "unit-tests": VerificationCommand("unit-tests", (sys.executable, "-c", "pass")),
                "security-scan": VerificationCommand("security-scan", (sys.executable, "-c", "pass")),
            },
        )
        self.store = VerificationEvidenceStore(
            self.root / "reports.sqlite3",
            execution_evidence_validator=self.executor.validate_evidence_reference,
            reviewer_authorizer=lambda actor, action, digest: actor.startswith(("builder", "implementation-", "reviewer-")) and action in {"verification:submit", "verification:review"} and len(digest) == 64,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, tests_reference, security_reference, *, digest=None):
        names = ("acceptance_criteria", "tests", "security", "architecture", "claims_and_provenance")
        checks = []
        for name in names:
            ref = tests_reference if name == "tests" else security_reference if name == "security" else "review://independent/" + name
            checks.append(VerificationCheck(
                name=name, reviewer_agent_id="reviewer-" + name, status="passed",
                evidence_reference=ref, summary="Reviewed " + name,
            ))
        return VerificationReport(
            artifact_sha256=digest or self.digest, author_agent_id="builder",
            checks=tuple(checks), created_at="2026-11-07T10:00:00+00:00",
        )

    def test_valid_artifact_bound_success_receipt_can_be_stored(self):
        receipt = self.executor.run("unit-tests", artifact_sha256=self.digest)
        security = self.executor.run("security-scan", artifact_sha256=self.digest)
        report = self.report(
            self.executor.evidence_reference(receipt),
            self.executor.evidence_reference(security),
        )
        stored = self.store.record(report)
        self.assertTrue(stored["decision"]["accepted"])

    def test_fabricated_receipt_reference_is_rejected(self):
        with self.assertRaisesRegex(VerificationStoreError, "execution_receipt_invalid"):
            security = self.executor.run("security-scan", artifact_sha256=self.digest)
            self.store.record(self.report(
                "yuki-verification-execution://" + "a" * 32,
                self.executor.evidence_reference(security),
            ))

    def test_receipt_for_different_artifact_is_rejected(self):
        receipt = self.executor.run("unit-tests", artifact_sha256="0" * 64)
        security = self.executor.run("security-scan", artifact_sha256=self.digest)
        with self.assertRaisesRegex(VerificationStoreError, "execution_receipt_invalid"):
            self.store.record(self.report(
                self.executor.evidence_reference(receipt),
                self.executor.evidence_reference(security),
            ))

    def test_failed_execution_cannot_support_passed_test_check(self):
        failing = LocalVerificationExecutor(
            self.root, self.root / "failed.sqlite3",
            {"unit-tests": VerificationCommand(
                "unit-tests", (sys.executable, "-c", "raise SystemExit(9)")
            )},
        )
        receipt = failing.run("unit-tests", artifact_sha256=self.digest)
        security = self.executor.run("security-scan", artifact_sha256=self.digest)
        failing_store = VerificationEvidenceStore(
            self.root / "failed-reports.sqlite3",
            execution_evidence_validator=failing.validate_evidence_reference,
            reviewer_authorizer=lambda actor, action, digest: actor.startswith(("builder", "implementation-", "reviewer-")) and action in {"verification:submit", "verification:review"} and len(digest) == 64,
        )
        with self.assertRaisesRegex(VerificationStoreError, "execution_receipt_invalid"):
            failing_store.record(self.report(
                failing.evidence_reference(receipt),
                self.executor.evidence_reference(security),
            ))

    def test_configured_test_and_security_checks_require_receipts(self):
        with self.assertRaisesRegex(VerificationStoreError, "execution_receipt_required"):
            self.store.record(self.report(
                "review://claimed-tests-passed",
                "review://claimed-security-passed",
            ))

    def test_receipt_from_wrong_command_cannot_substitute_for_security_scan(self):
        tests = self.executor.run("unit-tests", artifact_sha256=self.digest)
        second_tests = self.executor.run("unit-tests", artifact_sha256=self.digest)
        with self.assertRaisesRegex(VerificationStoreError, "execution_receipt_invalid"):
            self.store.record(self.report(
                self.executor.evidence_reference(tests),
                self.executor.evidence_reference(second_tests),
            ))


if __name__ == "__main__":
    unittest.main()
