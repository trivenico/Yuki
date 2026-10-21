import subprocess
import unittest
from unittest.mock import patch

from core.container_recovery import (
    ContainerRecoveryError,
    container_name_for_execution,
    reconcile_execution_container,
)


class ContainerRecoveryTests(unittest.TestCase):
    def test_container_name_is_deterministic_and_bounded(self):
        self.assertEqual(container_name_for_execution("run-1"), container_name_for_execution("run-1"))
        self.assertLessEqual(len(container_name_for_execution("x" * 128)), 64)

    def test_invalid_execution_id_rejected_before_docker(self):
        with patch("core.container_recovery.subprocess.run") as run:
            for value in ("", "../escape", "space id", "x" * 129):
                with self.subTest(value=value):
                    with self.assertRaises(ContainerRecoveryError):
                        reconcile_execution_container(value)
            run.assert_not_called()

    @patch("core.container_recovery.subprocess.run")
    def test_verified_owned_container_removed(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "/yuki-job-abc\ttrue\trun-1\n", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        # Patch the deterministic name into inspect output to ensure exact ownership validation.
        name = container_name_for_execution("run-1")
        run.side_effect = [
            subprocess.CompletedProcess([], 0, f"{'a' * 64}\t/{name}\ttrue\trun-1\n", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        result = reconcile_execution_container("run-1")
        self.assertTrue(result.removed)
        self.assertFalse(result.already_absent)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0][1:3], ["rm", "-f"])
        self.assertEqual(run.call_args.args[0][3], "a" * 64)

    @patch("core.container_recovery.subprocess.run")
    def test_claim_callback_rejection_prevents_docker_remove(self, run):
        name = container_name_for_execution("run-1")
        run.return_value = subprocess.CompletedProcess([], 0, f"{'a' * 64}\t/{name}\ttrue\trun-1\n", "")
        with self.assertRaisesRegex(ContainerRecoveryError, "claim expired or changed"):
            reconcile_execution_container("run-1", before_remove=lambda: False)
        run.assert_called_once()

    @patch("core.container_recovery.subprocess.run")
    def test_claim_callback_exception_fails_closed(self, run):
        name = container_name_for_execution("run-1")
        run.return_value = subprocess.CompletedProcess([], 0, f"{'a' * 64}\t/{name}\ttrue\trun-1\n", "")
        def fail_claim():
            raise RuntimeError("journal unavailable")
        with self.assertRaisesRegex(ContainerRecoveryError, "could not be verified"):
            reconcile_execution_container("run-1", before_remove=fail_claim)
        run.assert_called_once()
    @patch("core.container_recovery.subprocess.run")
    def test_wrong_owner_refuses_cleanup(self, run):
        name = container_name_for_execution("run-1")
        run.return_value = subprocess.CompletedProcess([], 0, f"{'a' * 64}\t/{name}\tfalse\trun-1\n", "")
        with self.assertRaisesRegex(ContainerRecoveryError, "ownership labels"):
            reconcile_execution_container("run-1")
        run.assert_called_once()

    @patch("core.container_recovery.subprocess.run")
    def test_wrong_execution_label_refuses_cleanup(self, run):
        name = container_name_for_execution("run-1")
        run.return_value = subprocess.CompletedProcess([], 0, f"{'a' * 64}\t/{name}\ttrue\tother-run\n", "")
        with self.assertRaisesRegex(ContainerRecoveryError, "ownership labels"):
            reconcile_execution_container("run-1")
        run.assert_called_once()

    @patch("core.container_recovery.subprocess.run")
    def test_missing_container_is_idempotent(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "", "Error: No such container: missing")
        result = reconcile_execution_container("run-1")
        self.assertTrue(result.already_absent)
        self.assertFalse(result.removed)
        run.assert_called_once()

    @patch("core.container_recovery.subprocess.run")
    def test_docker_timeout_fails_closed(self, run):
        run.side_effect = subprocess.TimeoutExpired("docker", 5)
        with self.assertRaisesRegex(ContainerRecoveryError, "could not be queried"):
            reconcile_execution_container("run-1")
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
