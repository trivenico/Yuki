try:
    import fcntl
except ImportError:  # pragma: no cover - Linux-only runner tests
    fcntl = None
import hashlib
import os
import unittest

from evaluation.runner_isolation_policy import create_runner_isolation_policy
from evaluation.runner_supervisor import (
    RunnerLaunchState,
    RunnerWaitResult,
    run_supervised_attempt,
)


class FakeSupervisor:
    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.calls = []

    def _call(self, name, result=None):
        self.calls.append(name)
        if self.fail_at == name:
            raise RuntimeError(name + "_failed")
        return result

    def create_cgroup(self, policy):
        return self._call("create_cgroup", "cg")

    def apply_policy(self, cgroup, settings):
        self._call("apply_policy")

    def verify_policy(self, cgroup, policy):
        return self._call("verify_policy", True)

    def fork_blocked_child(self, cgroup, artifact_descriptor):
        self.artifact_descriptor = artifact_descriptor
        self.artifact_was_inheritable = os.get_inheritable(artifact_descriptor)
        return self._call("fork_blocked_child", "child")

    def verify_child_identity(self, child):
        return self._call("verify_child_identity", True)

    def verify_descriptor_allowlist(self, child, artifact_descriptor):
        self.allowlist_artifact_descriptor = artifact_descriptor
        return self._call("verify_descriptor_allowlist", True)

    def verify_exec_barrier(self, child):
        return self._call("verify_exec_barrier", True)

    def place_child(self, child, cgroup):
        self._call("place_child")

    def verify_membership(self, child, cgroup):
        return self._call("verify_membership", True)

    def verify_artifact(self, child, artifact_descriptor, expected_sha256):
        self.artifact_verified = os.fstat(artifact_descriptor).st_size >= 0
        self.expected_sha256 = expected_sha256
        return self._call("verify_artifact", True)

    def release_exec(self, child):
        self._call("release_exec")

    def wait_for_child(self, child, timeout_seconds):
        return self._call("wait_for_child", RunnerWaitResult(0, False))

    def abort_and_reap_child(self, child):
        self._call("abort_and_reap_child")

    def verify_child_reaped(self, child):
        return self._call("verify_child_reaped", True)

    def verify_cgroup_empty(self, cgroup):
        return self._call("verify_cgroup_empty", True)

    def remove_cgroup(self, cgroup):
        self._call("remove_cgroup")


@unittest.skipUnless(
    fcntl is not None and hasattr(os, "memfd_create")
    and hasattr(fcntl, "F_ADD_SEALS"),
    "Linux memfd sealing is required for runner supervisor tests",
)
class RunnerSupervisorContractTests(unittest.TestCase):
    def setUp(self):
        self.artifact_bytes = b"yuki-runner-test-artifact"
        self.artifact = os.memfd_create(
            "yuki-runner-test-artifact",
            flags=os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
        )
        os.write(self.artifact, self.artifact_bytes)
        required_seals = (
            fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
        )
        fcntl.fcntl(self.artifact, fcntl.F_ADD_SEALS, required_seals)
        os.lseek(self.artifact, 0, os.SEEK_SET)
        self.artifact_sha256 = hashlib.sha256(self.artifact_bytes).hexdigest()
        self.policy = create_runner_isolation_policy(
            uid=10001, gid=10001, seccomp_profile_digest="a" * 64
        )

    def tearDown(self):
        os.close(self.artifact)

    def test_successful_launch_obeys_pre_exec_order(self):
        adapter = FakeSupervisor()
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=self.artifact,
            expected_artifact_sha256=self.artifact_sha256
        )
        self.assertEqual(result.state, RunnerLaunchState.CLEANED)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.artifact_sha256, self.artifact_sha256)
        self.assertEqual(result.policy_digest, self.policy.policy_digest)
        self.assertLess(adapter.calls.index("verify_child_identity"),
                        adapter.calls.index("verify_descriptor_allowlist"))
        self.assertLess(adapter.calls.index("verify_descriptor_allowlist"),
                        adapter.calls.index("verify_exec_barrier"))
        self.assertLess(adapter.calls.index("verify_exec_barrier"),
                        adapter.calls.index("place_child"))
        self.assertLess(adapter.calls.index("verify_membership"),
                        adapter.calls.index("verify_artifact"))
        self.assertLess(adapter.calls.index("verify_artifact"),
                        adapter.calls.index("release_exec"))
        self.assertLess(adapter.calls.index("release_exec"),
                        adapter.calls.index("wait_for_child"))
        self.assertLess(adapter.calls.index("verify_child_reaped"),
                        adapter.calls.index("verify_cgroup_empty"))
        self.assertLess(adapter.calls.index("verify_cgroup_empty"),
                        adapter.calls.index("remove_cgroup"))
        self.assertEqual(adapter.calls[-1], "remove_cgroup")

    def test_each_pre_exec_failure_prevents_exec_release_and_cleans_up(self):
        stages = (
            "create_cgroup", "apply_policy", "verify_policy",
            "fork_blocked_child", "verify_child_identity",
            "verify_descriptor_allowlist", "verify_exec_barrier",
            "place_child", "verify_membership",
        )
        for stage in stages:
            with self.subTest(stage=stage):
                adapter = FakeSupervisor(fail_at=stage)
                result = run_supervised_attempt(
                    adapter=adapter, policy=self.policy, artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256
                )
                self.assertEqual(result.state, RunnerLaunchState.FAILED)
                self.assertNotIn("release_exec", adapter.calls)
                if stage != "create_cgroup":
                    self.assertIn("remove_cgroup", adapter.calls)
                if stage in (
                    "verify_child_identity", "verify_descriptor_allowlist",
                    "verify_exec_barrier", "place_child", "verify_membership",
                ):
                    self.assertIn("abort_and_reap_child", adapter.calls)

    def test_false_membership_result_prevents_exec_release(self):
        adapter = FakeSupervisor()
        adapter.verify_membership = lambda child, cgroup: False
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertNotIn("release_exec", adapter.calls)
        self.assertIn("abort_and_reap_child", adapter.calls)

    def test_each_child_trust_gate_fails_closed(self):
        gates = (
            ("verify_child_identity", lambda child: False),
            ("verify_descriptor_allowlist", lambda child, descriptor: False),
            ("verify_exec_barrier", lambda child: False),
        )
        for method, reject in gates:
            with self.subTest(gate=method):
                adapter = FakeSupervisor()
                setattr(adapter, method, reject)
                result = run_supervised_attempt(
                    adapter=adapter, policy=self.policy,
                    artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256,
                )
                self.assertEqual(result.state, RunnerLaunchState.FAILED)
                self.assertNotIn("release_exec", adapter.calls)
                self.assertIn("abort_and_reap_child", adapter.calls)
                self.assertIn("verify_child_reaped", adapter.calls)

    def test_false_policy_verification_prevents_child_creation(self):
        adapter = FakeSupervisor()
        adapter.verify_policy = lambda cgroup, policy: False
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertNotIn("fork_blocked_child", adapter.calls)
        self.assertNotIn("release_exec", adapter.calls)

    def test_keyboard_interrupt_still_reaps_child_and_cleans_cgroup(self):
        adapter = FakeSupervisor()
        adapter.wait_for_child = lambda child, timeout: (_ for _ in ()).throw(
            KeyboardInterrupt()
        )
        with self.assertRaises(KeyboardInterrupt):
            run_supervised_attempt(
                adapter=adapter, policy=self.policy, artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256
            )
        self.assertIn("abort_and_reap_child", adapter.calls)
        self.assertIn("verify_child_reaped", adapter.calls)
        self.assertIn("verify_cgroup_empty", adapter.calls)
        self.assertIn("remove_cgroup", adapter.calls)

    def test_cancellation_during_abort_is_preserved_and_blocks_removal(self):
        adapter = FakeSupervisor()
        adapter.wait_for_child = lambda child, timeout: RunnerWaitResult(None, True)
        adapter.abort_and_reap_child = lambda child: (_ for _ in ()).throw(
            KeyboardInterrupt()
        )
        with self.assertRaises(KeyboardInterrupt):
            run_supervised_attempt(
                adapter=adapter, policy=self.policy, artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256
            )
        self.assertNotIn("remove_cgroup", adapter.calls)

    def test_cancellation_during_cgroup_empty_check_is_preserved(self):
        adapter = FakeSupervisor()
        adapter.verify_cgroup_empty = lambda cgroup: (_ for _ in ()).throw(
            KeyboardInterrupt()
        )
        with self.assertRaises(KeyboardInterrupt):
            run_supervised_attempt(
                adapter=adapter, policy=self.policy, artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256
            )
        self.assertNotIn("remove_cgroup", adapter.calls)

    def test_timeout_aborts_and_reaps_before_cgroup_cleanup(self):
        adapter = FakeSupervisor()
        adapter.wait_for_child = lambda child, timeout: RunnerWaitResult(None, True)
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertIn("abort_and_reap_child", adapter.calls)
        self.assertIn("verify_child_reaped", adapter.calls)
        self.assertIn("verify_cgroup_empty", adapter.calls)
        self.assertIn("remove_cgroup", adapter.calls)
        self.assertIn("TimeoutError:exec_released", result.failure)

    def test_wait_timeout_exception_still_requires_abort_and_verified_cleanup(self):
        adapter = FakeSupervisor()
        adapter.wait_for_child = lambda child, timeout: (_ for _ in ()).throw(
            TimeoutError("wait deadline exceeded")
        )
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertIn("abort_and_reap_child", adapter.calls)
        self.assertIn("verify_child_reaped", adapter.calls)
        self.assertIn("verify_cgroup_empty", adapter.calls)
        self.assertIn("remove_cgroup", adapter.calls)

    def test_timeout_abort_failure_prevents_cgroup_removal(self):
        adapter = FakeSupervisor(fail_at="abort_and_reap_child")
        adapter.wait_for_child = lambda child, timeout: RunnerWaitResult(None, True)
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertNotIn("verify_cgroup_empty", adapter.calls)
        self.assertNotIn("remove_cgroup", adapter.calls)
        self.assertIn("child_cleanup_failed", result.failure)

    def test_malformed_timeout_result_fails_closed(self):
        adapter = FakeSupervisor()
        adapter.wait_for_child = lambda child, timeout: RunnerWaitResult(0, True)
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertIn("abort_and_reap_child", adapter.calls)
        self.assertIn("remove_cgroup", adapter.calls)

    def test_ordinary_abort_failure_is_reported_without_masking_result(self):
        adapter = FakeSupervisor(fail_at="abort_and_reap_child")
        adapter.wait_for_child = lambda child, timeout: RunnerWaitResult(None, True)
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertIn("child_cleanup_failed", result.failure)
        self.assertNotIn("child_cleanup_interrupted", result.failure)
        self.assertNotIn("verify_cgroup_empty", adapter.calls)
        self.assertNotIn("remove_cgroup", adapter.calls)

    def test_cleanup_failure_is_reported_not_hidden(self):
        adapter = FakeSupervisor(fail_at="remove_cgroup")
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertIn("cgroup_cleanup_unverified", result.failure)

    def test_unverified_reap_prevents_cgroup_removal(self):
        adapter = FakeSupervisor()
        adapter.verify_child_reaped = lambda child: False
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertNotIn("remove_cgroup", adapter.calls)
        self.assertIn("cgroup_cleanup_unverified", result.failure)

    def test_nonempty_cgroup_prevents_removal(self):
        adapter = FakeSupervisor()
        adapter.verify_cgroup_empty = lambda cgroup: False
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertNotIn("remove_cgroup", adapter.calls)
        self.assertIn("cgroup_cleanup_unverified", result.failure)


    def test_adapter_receives_pinned_non_inheritable_artifact_descriptor(self):
        adapter = FakeSupervisor()
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy,
            artifact_descriptor=self.artifact,
                    expected_artifact_sha256=self.artifact_sha256,
        )
        self.assertEqual(result.state, RunnerLaunchState.CLEANED)
        self.assertFalse(adapter.artifact_was_inheritable)
        self.assertNotEqual(adapter.artifact_descriptor, self.artifact)
        self.assertTrue(os.fstat(self.artifact))
        with self.assertRaises(OSError):
            os.fstat(adapter.artifact_descriptor)


    def test_artifact_digest_mismatch_fails_before_adapter_calls(self):
        adapter = FakeSupervisor()
        with self.assertRaises(ValueError):
            run_supervised_attempt(
                adapter=adapter, policy=self.policy,
                artifact_descriptor=self.artifact,
                expected_artifact_sha256="0" * 64,
            )
        self.assertEqual(adapter.calls, [])

    def test_child_artifact_binding_must_verify_before_exec_release(self):
        adapter = FakeSupervisor()
        adapter.verify_artifact = lambda child, descriptor, digest: False
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy,
            artifact_descriptor=self.artifact,
            expected_artifact_sha256=self.artifact_sha256,
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertNotIn("release_exec", adapter.calls)
        self.assertIn("abort_and_reap_child", adapter.calls)

    def test_sealed_artifact_cannot_be_mutated_during_blocked_staging(self):
        adapter = FakeSupervisor()

        def attempt_mutation_after_binding(child, descriptor, digest):
            with self.assertRaises(OSError):
                os.pwrite(descriptor, b"tampered", 0)
            return True

        adapter.verify_artifact = attempt_mutation_after_binding
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy,
            artifact_descriptor=self.artifact,
            expected_artifact_sha256=self.artifact_sha256,
        )
        self.assertEqual(result.state, RunnerLaunchState.CLEANED)
        self.assertIn("release_exec", adapter.calls)

    def test_unsealed_regular_file_is_rejected_before_adapter_calls(self):
        adapter = FakeSupervisor()
        unsealed = os.memfd_create("yuki-unsealed-artifact", os.MFD_CLOEXEC)
        try:
            os.write(unsealed, self.artifact_bytes)
            with self.assertRaisesRegex(ValueError, "sealed memfd"):
                run_supervised_attempt(
                    adapter=adapter, policy=self.policy,
                    artifact_descriptor=unsealed,
                    expected_artifact_sha256=self.artifact_sha256,
                )
            self.assertEqual(adapter.calls, [])
        finally:
            os.close(unsealed)

    def test_non_regular_artifact_descriptor_is_rejected_before_adapter_calls(self):
        adapter = FakeSupervisor()
        read_fd, write_fd = os.pipe()
        try:
            with self.assertRaises(ValueError):
                run_supervised_attempt(
                    adapter=adapter, policy=self.policy,
                    artifact_descriptor=read_fd,
                    expected_artifact_sha256=self.artifact_sha256,
                )
            self.assertEqual(adapter.calls, [])
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_invalid_policy_or_descriptor_fails_before_adapter_calls(self):
        adapter = FakeSupervisor()
        with self.assertRaises(ValueError):
            run_supervised_attempt(
                adapter=adapter, policy=self.policy, artifact_descriptor=True,
                expected_artifact_sha256=self.artifact_sha256,
            )
        self.assertEqual(adapter.calls, [])


if __name__ == "__main__":
    unittest.main()
