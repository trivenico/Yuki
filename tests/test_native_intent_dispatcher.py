import unittest

from core.native_intents import IntentKind, NativeIntentParser
from core.native_intent_dispatcher import (
    NativeDispatchAuditError,
    NativeDispatchDenied,
    NativeIntentDispatcher,
)


class NativeIntentDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.events = []
        self.dispatcher = NativeIntentDispatcher(
            NativeIntentParser(),
            {
                IntentKind.HELP: lambda actor, args: "help",
                IntentKind.STATUS: lambda actor, args: self._record(actor, args),
                IntentKind.MEMORY_REMEMBER: lambda actor, args: "saved",
                IntentKind.PLAN_CREATE: lambda actor, args: "planned",
            },
            authorize=lambda actor, kind: actor == "user-1",
            audit=self.events.append,
        )

    def _record(self, actor, args):
        self.calls.append((actor, args))
        return "status"

    def test_authorized_read_dispatches_and_audits_before_handler(self):
        result = self.dispatcher.dispatch("user-1", "/status")
        self.assertTrue(result.executed)
        self.assertEqual(result.output, "status")
        self.assertEqual(self.calls, [("user-1", ())])
        self.assertEqual(self.events[0]["intent"], "status")

    def test_unauthorized_actor_never_reaches_handler_or_audit(self):
        with self.assertRaises(NativeDispatchDenied):
            self.dispatcher.dispatch("user-2", "/status")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.events, [])

    def test_mutating_intent_requires_explicit_confirmation(self):
        with self.assertRaises(NativeDispatchDenied):
            self.dispatcher.dispatch("user-1", "/memory remember a secret")
        self.assertEqual(self.events, [])
        result = self.dispatcher.dispatch(
            "user-1", "/memory remember a secret", confirmed=True
        )
        self.assertTrue(result.executed)

    def test_plan_creation_requires_confirmation(self):
        with self.assertRaises(NativeDispatchDenied):
            self.dispatcher.dispatch("user-1", "/plan prepare release")
        result = self.dispatcher.dispatch(
            "user-1", "/plan prepare release", confirmed=True
        )
        self.assertEqual(result.output, "planned")

    def test_unknown_or_unregistered_intents_do_not_execute(self):
        unknown = self.dispatcher.dispatch("user-1", "delete everything")
        self.assertFalse(unknown.executed)
        unavailable = self.dispatcher.dispatch("user-1", "/task list")
        self.assertFalse(unavailable.executed)
        self.assertEqual(self.events, [])

    def test_audit_failure_fails_closed_before_handler(self):
        dispatcher = NativeIntentDispatcher(
            NativeIntentParser(),
            {IntentKind.STATUS: lambda actor, args: self._record(actor, args)},
            authorize=lambda actor, kind: True,
            audit=lambda event: (_ for _ in ()).throw(OSError("disk full")),
        )
        with self.assertRaises(NativeDispatchAuditError):
            dispatcher.dispatch("user-1", "/status")
        self.assertEqual(self.calls, [])

    def test_authorizer_exception_fails_closed(self):
        dispatcher = NativeIntentDispatcher(
            NativeIntentParser(),
            {IntentKind.STATUS: lambda actor, args: "unsafe"},
            authorize=lambda actor, kind: 1 / 0,
            audit=self.events.append,
        )
        with self.assertRaises(NativeDispatchDenied):
            dispatcher.dispatch("user-1", "/status")
        self.assertEqual(self.events, [])

    def test_invalid_confirmation_type_is_rejected(self):
        with self.assertRaises(TypeError):
            self.dispatcher.dispatch("user-1", "/status", confirmed=1)


if __name__ == "__main__":
    unittest.main()
