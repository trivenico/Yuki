import asyncio
import unittest
from core.self_healing import RecoveryStatus, SelfHealingManager

class SelfHealingTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_registered_recovery_runs(self):
        calls = []
        async def recover(): calls.append("ran"); return True
        manager = SelfHealingManager({"ollama": recover}, cooldown_seconds=0)
        unknown = await manager.recover("shell")
        self.assertEqual(unknown.status, RecoveryStatus.UNKNOWN_COMPONENT)
        self.assertEqual(calls, [])
        result = await manager.recover("ollama")
        self.assertEqual(result.status, RecoveryStatus.RECOVERED)
        self.assertEqual(calls, ["ran"])

    async def test_cooldown_prevents_repeat(self):
        now = [10.0]
        calls = []
        async def recover(): calls.append(1); return True
        manager = SelfHealingManager({"voice": recover}, cooldown_seconds=30, clock=lambda: now[0])
        self.assertEqual((await manager.recover("voice")).status, RecoveryStatus.RECOVERED)
        self.assertEqual((await manager.recover("voice")).status, RecoveryStatus.COOLDOWN)
        self.assertEqual(len(calls), 1)
        now[0] = 50
        self.assertEqual((await manager.recover("voice")).status, RecoveryStatus.RECOVERED)

    async def test_failure_and_timeout_are_contained(self):
        async def bad(): raise RuntimeError("secret detail")
        async def slow(): await asyncio.sleep(0.1); return True
        manager = SelfHealingManager({"bad": bad, "slow": slow}, cooldown_seconds=0, action_timeout_seconds=0.01)
        result = await manager.recover("bad")
        self.assertEqual(result.status, RecoveryStatus.FAILED)
        self.assertNotIn("secret detail", result.message)
        self.assertEqual((await manager.recover("slow")).status, RecoveryStatus.FAILED)

    async def test_concurrent_recovery_is_busy(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        async def recover():
            entered.set()
            await release.wait()
            return True
        manager = SelfHealingManager({"db": recover}, cooldown_seconds=0)
        first = asyncio.create_task(manager.recover("db"))
        await entered.wait()
        second = await manager.recover("db")
        self.assertEqual(second.status, RecoveryStatus.BUSY)
        release.set()
        self.assertEqual((await first).status, RecoveryStatus.RECOVERED)

if __name__ == "__main__": unittest.main()
