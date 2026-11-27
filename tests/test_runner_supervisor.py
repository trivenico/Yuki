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
        return self._call("fork_blocked_child", "child")

    def place_child(self, child, cgroup):
        self._call("place_child")

    def verify_membership(self, child, cgroup):
        return self._call("verify_membership", True)

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


class RunnerSupervisorContractTests(unittest.TestCase):
    def setUp(self):
        self.policy = create_runner_isolation_policy(
            uid=10001, gid=10001, seccomp_profile_digest="a" * 64
        )

    def test_successful_launch_obeys_pre_exec_order(self):
        adapter = FakeSupervisor()
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=9
        )
        self.assertEqual(result.state, RunnerLaunchState.CLEANED)
        self.assertEqual(result.exit_code, 0)
        self.assertLess(adapter.calls.index("verify_membership"),
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
            "fork_blocked_child", "place_child", "verify_membership",
        )
        for stage in stages:
            with self.subTest(stage=stage):
                adapter = FakeSupervisor(fail_at=stage)
                result = run_supervised_attempt(
                    adapter=adapter, policy=self.policy, artifact_descriptor=9
                )
                self.assertEqual(result.state, RunnerLaunchState.FAILED)
                self.assertNotIn("release_exec", adapter.calls)
                if stage != "create_cgroup":
                    self.assertIn("remove_cgroup", adapter.calls)
                if stage in ("place_child", "verify_membership"):
                    self.assertIn("abort_and_reap_child", adapter.calls)

    def test_false_membership_result_prevents_exec_release(self):
        adapter = FakeSupervisor()
        adapter.verify_membership = lambda child, cgroup: False
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=9
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertNotIn("release_exec", adapter.calls)
        self.assertIn("abort_and_reap_child", adapter.calls)

    def test_false_policy_verification_prevents_child_creation(self):
        adapter = FakeSupervisor()
        adapter.verify_policy = lambda cgroup, policy: False
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=9
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertNotIn("fork_blocked_child", adapter.calls)
        self.assertNotIn("release_exec", adapter.calls)

    def test_timeout_aborts_and_reaps_before_cgroup_cleanup(self):
        adapter = FakeSupervisor()
        adapter.wait_for_child = lambda child, timeout: RunnerWaitResult(None, True)
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=9
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
            adapter=adapter, policy=self.policy, artifact_descriptor=9
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
            adapter=adapter, policy=self.policy, artifact_descriptor=9
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertNotIn("verify_cgroup_empty", adapter.calls)
        self.assertNotIn("remove_cgroup", adapter.calls)
        self.assertIn("child_cleanup_failed", result.failure)

    def test_malformed_timeout_result_fails_closed(self):
        adapter = FakeSupervisor()
        adapter.wait_for_child = lambda child, timeout: RunnerWaitResult(0, True)
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=9
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertIn("abort_and_reap_child", adapter.calls)
        self.assertIn("remove_cgroup", adapter.calls)

    def test_cleanup_failure_is_reported_not_hidden(self):
        adapter = FakeSupervisor(fail_at="remove_cgroup")
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=9
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertIn("cgroup_cleanup_failed", result.failure)

    def test_unverified_reap_prevents_cgroup_removal(self):
        adapter = FakeSupervisor()
        adapter.verify_child_reaped = lambda child: False
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=9
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertNotIn("remove_cgroup", adapter.calls)
        self.assertIn("cgroup_cleanup_unverified", result.failure)

    def test_nonempty_cgroup_prevents_removal(self):
        adapter = FakeSupervisor()
        adapter.verify_cgroup_empty = lambda cgroup: False
        result = run_supervised_attempt(
            adapter=adapter, policy=self.policy, artifact_descriptor=9
        )
        self.assertEqual(result.state, RunnerLaunchState.FAILED)
        self.assertNotIn("remove_cgroup", adapter.calls)
        self.assertIn("cgroup_cleanup_unverified", result.failure)

    def test_invalid_policy_or_descriptor_fails_before_adapter_calls(self):
        adapter = FakeSupervisor()
        with self.assertRaises(ValueError):
            run_supervised_attempt(
                adapter=adapter, policy=self.policy, artifact_descriptor=True
            )
        self.assertEqual(adapter.calls, [])


if __name__ == "__main__":
    unittest.main()
