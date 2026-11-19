import asyncio
import unittest
from types import SimpleNamespace

from api.runtime import ConversationRuntime


class WorkforceStartupRecoveryTests(unittest.TestCase):
    def test_expired_workforce_leases_recovered_before_startup_completes(self):
        calls = []
        workforce = SimpleNamespace(
            engine=SimpleNamespace(
                recover_expired_leases=lambda: calls.append("recovered") or 2
            ),
            close=lambda: calls.append("workforce_closed"),
        )
        application = SimpleNamespace(
            store=object(),
            close=lambda: calls.append("application_closed"),
        )
        runtime = ConversationRuntime(object(), application, workforce)
        events = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        sent = []

        async def receive():
            return events.pop(0)

        async def send(message):
            sent.append(message)

        asyncio.run(runtime({"type": "lifespan"}, receive, send))

        self.assertEqual(calls, ["recovered", "workforce_closed", "application_closed"])
        self.assertEqual(sent, [
            {"type": "lifespan.startup.complete"},
            {"type": "lifespan.shutdown.complete"},
        ])

    def test_invalid_lease_recovery_result_fails_startup_closed(self):
        calls = []
        workforce = SimpleNamespace(
            engine=SimpleNamespace(recover_expired_leases=lambda: -1),
            close=lambda: calls.append("workforce_closed"),
        )
        application = SimpleNamespace(
            store=object(),
            close=lambda: calls.append("application_closed"),
        )
        runtime = ConversationRuntime(object(), application, workforce)
        events = [{"type": "lifespan.startup"}]
        sent = []

        async def receive():
            return events.pop(0)

        async def send(message):
            sent.append(message)

        asyncio.run(runtime({"type": "lifespan"}, receive, send))

        self.assertEqual(sent, [{
            "type": "lifespan.startup.failed",
            "message": "Yuki startup recovery failed: RuntimeError",
        }])
        self.assertFalse(runtime._startup_complete)
        self.assertTrue(runtime._closed)
        self.assertEqual(calls, ["workforce_closed", "application_closed"])


if __name__ == "__main__":
    unittest.main()
