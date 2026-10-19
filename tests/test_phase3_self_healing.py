import asyncio
import unittest

from core.self_healing import RecoveryStatus, SelfHealingManager


class SelfHealingTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_action_recovers(self):
        manager = SelfHealingManager(cooldown_seconds=30)

        async def action():
            return True

        manager.register("cache", action)
        result = await manager.recover("cache")
        self.assertEqual(result.status, RecoveryStatus.RECOVERED)

    async def test_unknown_component_never_runs_action(self):
        manager = SelfHealingManager()
        result = await manager.recover("unregistered")
        self.assertEqual(result.status, RecoveryStatus.UNKNOWN_COMPONENT)

    async def test_cooldown_blocks_repeated_recovery(self):
        manager = SelfHealingManager(cooldown_seconds=30)
        calls = 0

        async def action():
            nonlocal calls
            calls += 1
            return True

        manager.register("ollama", action)
        first = await manager.recover("ollama")
        second = await manager.recover("ollama")
        self.assertEqual(first.status, RecoveryStatus.RECOVERED)
        self.assertEqual(second.status, RecoveryStatus.SKIPPED_COOLDOWN)
        self.assertEqual(calls, 1)

    async def test_recovery_timeout_is_bounded(self):
        manager = SelfHealingManager(action_timeout_seconds=0.01)

        async def action():
            await asyncio.sleep(1)
            return True

        manager.register("slow", action)
        result = await manager.recover("slow")
        self.assertEqual(result.status, RecoveryStatus.FAILED)
        self.assertEqual(result.detail, "recovery action timed out")

    async def test_callback_exception_is_sanitized(self):
        manager = SelfHealingManager()

        async def action():
            raise RuntimeError("token=private-secret")

        manager.register("broken", action)
        result = await manager.recover("broken")
        self.assertEqual(result.status, RecoveryStatus.FAILED)
        self.assertEqual(result.detail, "RuntimeError")
        self.assertNotIn("private-secret", result.detail)

    async def test_duplicate_component_registration_rejected(self):
        manager = SelfHealingManager()

        async def action():
            return True

        manager.register("cache", action)
        with self.assertRaises(ValueError):
            manager.register("cache", action)


if __name__ == "__main__":
    unittest.main()
