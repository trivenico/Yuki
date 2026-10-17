import unittest

from core.self_healing import (
    HealthStatus, RecoveryStatus, SelfHealingManager,
)


class SelfHealingTests(unittest.TestCase):
    def test_check_maps_boolean(self):
        manager = SelfHealingManager()
        manager.register_check("ollama", lambda: True)
        self.assertEqual(manager.check("ollama").status, HealthStatus.HEALTHY)

    def test_approval_required_fails_closed(self):
        actions = []
        manager = SelfHealingManager()
        manager.register_recovery("ollama", lambda: actions.append("restart"),
                                  verify=lambda: True, approval_required=True)
        result = manager.recover("ollama")
        self.assertEqual(result.status, RecoveryStatus.DENIED)
        self.assertEqual(actions, [])

    def test_recovery_runs_and_verifies_after_approval(self):
        state = {"healthy": False, "runs": 0}
        manager = SelfHealingManager(approval=lambda name, reason: True)
        manager.register_recovery(
            "cache",
            lambda: (state.update(healthy=True), state.update(runs=state["runs"] + 1)),
            verify=lambda: state["healthy"], cooldown=0, max_recoveries=2,
        )
        result = manager.recover("cache", reason="health failed")
        self.assertEqual(result.status, RecoveryStatus.RECOVERED)
        self.assertEqual(state["runs"], 1)

    def test_missing_recovery_is_reported(self):
        manager = SelfHealingManager()
        self.assertEqual(manager.recover("unknown").status, RecoveryStatus.NO_HANDLER)


if __name__ == "__main__":
    unittest.main()
