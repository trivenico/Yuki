import json
import unittest
from dataclasses import dataclass
from unittest.mock import Mock

from core.native_intents import IntentKind
from automation.task_runtime import TaskRuntime
from core.native_task_command_adapter import NativeTaskCommandAdapter


@dataclass
class Summary:
    task_id: str = "task-1"
    name: str = "release"
    state: str = "queued"
    version: int = 2
    attempts: int = 0
    created_at: str = "2026-11-20T00:00:00+00:00"
    updated_at: str = "2026-11-20T00:00:00+00:00"


@dataclass
class Record:
    task_id: str = "task-1"
    name: str = "release"
    state: str = "queued"
    steps: list = None
    result: dict | None = None
    error_code: str | None = None
    version: int = 2
    attempts: int = 0
    created_at: str = "2026-11-20T00:00:00+00:00"
    updated_at: str = "2026-11-20T00:00:00+00:00"


class NativeTaskCommandAdapterTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Mock(spec=TaskRuntime)
        self.runtime.list_task_summaries.return_value = [Summary()]
        self.runtime.get.return_value = Record(steps=[{"secret": "do not expose"}], result={"token": "do not expose"})
        self.capability_checks = []
        self.adapter = NativeTaskCommandAdapter(
            self.runtime,
            authorize_capability=lambda actor, capability: self._authorize(actor, capability),
        )

    def _authorize(self, actor, capability):
        self.capability_checks.append((actor, capability))
        return actor == "user-1"

    def test_handlers_are_only_read_only_task_intents(self):
        self.assertEqual(
            set(self.adapter.handlers),
            {IntentKind.TASK_LIST, IntentKind.TASK_SHOW},
        )

    def test_list_uses_redacted_summaries_and_is_bounded(self):
        output = json.loads(self.adapter.handlers[IntentKind.TASK_LIST]("user-1", ()))
        self.assertEqual(output["tasks"][0]["task_id"], "task-1")
        self.assertNotIn("steps", output["tasks"][0])
        self.runtime.list_task_summaries.assert_called_once_with(actor_id="user-1", limit=20)

    def test_show_uses_task_runtime_and_redacts_result_and_steps(self):
        output = json.loads(self.adapter.handlers[IntentKind.TASK_SHOW]("user-1", ("task-1",)))
        self.assertEqual(output["step_count"], 1)
        self.assertNotIn("result", output)
        self.assertNotIn("steps", output)
        self.runtime.get.assert_called_once_with(actor_id="user-1", task_id="task-1")

    def test_capability_gate_fails_closed(self):
        self.assertTrue(self.adapter.authorize_intent("user-1", IntentKind.TASK_LIST))
        self.assertFalse(self.adapter.authorize_intent("user-2", IntentKind.TASK_LIST))
        self.assertFalse(self.adapter.authorize_intent("user-1", IntentKind.PLAN_CREATE))

    def test_capability_callback_exception_fails_closed(self):
        adapter = NativeTaskCommandAdapter(
            self.runtime,
            authorize_capability=lambda actor, capability: 1 / 0,
        )
        self.assertFalse(adapter.authorize_intent("user-1", IntentKind.TASK_SHOW))

    def test_constructor_rejects_unbounded_limit_and_wrong_runtime(self):
        with self.assertRaises(ValueError):
            NativeTaskCommandAdapter(self.runtime, authorize_capability=lambda *_: True, list_limit=1000)
        with self.assertRaises(TypeError):
            NativeTaskCommandAdapter(object(), authorize_capability=lambda *_: True)


if __name__ == "__main__":
    unittest.main()
