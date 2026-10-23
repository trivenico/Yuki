import unittest

from automation.workflow_engine import WorkflowEngine, WorkflowStep


class CooperativeCancellationTests(unittest.TestCase):
    def test_cancelled_before_first_step_prevents_handler(self):
        calls = []
        engine = WorkflowEngine(
            handlers={"emit": lambda text: calls.append(text) or text},
            approver=lambda *_: True,
        )
        result = engine.run(
            [WorkflowStep("first", "emit", "must-not-run")],
            cancellation_check=lambda: True,
        )
        self.assertEqual(calls, [])
        self.assertEqual(result, [{"step": "first", "ok": False, "status": "cancelled"}])

    def test_cancellation_after_approval_prevents_handler(self):
        calls = []
        checks = iter([False, True])
        engine = WorkflowEngine(
            handlers={"emit": lambda text: calls.append(text) or text},
            approver=lambda *_: True,
        )
        result = engine.run(
            [WorkflowStep("first", "emit", "must-not-run")],
            cancellation_check=lambda: next(checks),
        )
        self.assertEqual(calls, [])
        self.assertEqual(result[0]["status"], "cancelled")

    def test_cancellation_check_failure_fails_closed(self):
        calls = []
        def broken_check():
            raise RuntimeError("secret internal detail")
        engine = WorkflowEngine(
            handlers={"emit": lambda text: calls.append(text) or text},
            approver=lambda *_: True,
        )
        result = engine.run(
            [WorkflowStep("first", "emit", "must-not-run")],
            cancellation_check=broken_check,
        )
        self.assertEqual(calls, [])
        self.assertEqual(result[0]["status"], "cancellation_check_failed")
        self.assertNotIn("secret", repr(result))

    def test_invalid_cancellation_checker_is_rejected(self):
        with self.assertRaises(TypeError):
            WorkflowEngine(approver=lambda *_: True).run(
                [WorkflowStep("first", "note")], cancellation_check=True
            )

    def test_existing_run_behavior_without_checker_is_unchanged(self):
        engine = WorkflowEngine(approver=lambda *_: True)
        result = engine.run([WorkflowStep("first", "note", "safe")])
        self.assertEqual(result[0]["status"], "recorded")


if __name__ == "__main__":
    unittest.main()
