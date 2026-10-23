import unittest

from automation.workflow_engine import WorkflowEngine, WorkflowStep


class WorkflowEngineTests(unittest.TestCase):
    def test_requires_fresh_approval_by_default(self):
        engine = WorkflowEngine()
        result = engine.run([WorkflowStep("first", "emit", "hello")])
        self.assertEqual(result, [{"step": "first", "ok": False, "status": "approval_denied"}])

    def test_approval_must_be_exact_true(self):
        for approval_result in (False, None, 1, "yes"):
            engine = WorkflowEngine(approver=lambda *_args, value=approval_result: value)
            result = engine.run([WorkflowStep("first", "emit", "hello")])
            self.assertEqual(result[0]["status"], "approval_denied")

    def test_approved_handler_runs_and_output_is_bounded(self):
        calls = []
        engine = WorkflowEngine(
            handlers={"emit": lambda text: calls.append(text) or ("x" * 5000)},
            approver=lambda *_args: True,
        )
        result = engine.run([WorkflowStep("first", "emit", "hello")])
        self.assertEqual(calls, ["hello"])
        self.assertEqual(result[0]["status"], "completed")
        self.assertEqual(len(result[0]["output"]), WorkflowEngine.MAX_OUTPUT_CHARS)

    def test_denied_step_stops_following_steps(self):
        calls = []
        engine = WorkflowEngine(
            handlers={"emit": lambda text: calls.append(text) or text},
            approver=lambda name, *_args: name == "allowed",
        )
        result = engine.run([
            WorkflowStep("allowed", "emit", "one"),
            WorkflowStep("denied", "emit", "two"),
            WorkflowStep("never", "emit", "three"),
        ])
        self.assertEqual(calls, ["one"])
        self.assertEqual(result[-1]["status"], "approval_denied")
        self.assertEqual(len(result), 2)

    def test_wait_for_approval_cannot_disable_approval(self):
        with self.assertRaises(ValueError):
            WorkflowEngine().run([WorkflowStep("gate", "wait_for_approval", requires_approval=False)])

    def test_unknown_mapping_fields_are_rejected(self):
        with self.assertRaises(ValueError):
            WorkflowEngine(approver=lambda *_: True).run([
                {"name": "x", "action": "emit", "argument": "a", "shell": "danger"}
            ])

    def test_duplicate_names_and_nul_are_rejected(self):
        engine = WorkflowEngine(approver=lambda *_: True)
        with self.assertRaises(ValueError):
            engine.run([WorkflowStep("same", "note"), WorkflowStep("same", "note")])
        with self.assertRaises(ValueError):
            engine.run([WorkflowStep("bad\x00name", "note")])
        with self.assertRaises(ValueError):
            engine.run([WorkflowStep("bad", "note", "a\x00b")])

    def test_handlers_are_limited_to_safe_registered_actions(self):
        with self.assertRaises(ValueError):
            WorkflowEngine(handlers={"shell": lambda _: "no"})
        with self.assertRaises(TypeError):
            WorkflowEngine(handlers={"emit": "not-callable"})

    def test_handler_errors_are_redacted_and_stop_workflow(self):
        engine = WorkflowEngine(
            handlers={"emit": lambda _text: (_ for _ in ()).throw(RuntimeError("private secret"))},
            approver=lambda *_args: True,
        )
        result = engine.run([WorkflowStep("first", "emit"), WorkflowStep("second", "note")])
        self.assertEqual(result, [{"step": "first", "ok": False, "status": "handler_failed"}])

    def test_validates_all_steps_before_any_handler_runs(self):
        calls = []
        engine = WorkflowEngine(
            handlers={"emit": lambda text: calls.append(text) or text},
            approver=lambda *_args: True,
        )
        with self.assertRaises(ValueError):
            engine.run([
                WorkflowStep("first", "emit", "side-effect"),
                {"name": "invalid", "action": "not-allowed"},
            ])
        self.assertEqual(calls, [])

    def test_invalid_workflow_bounds_are_rejected(self):
        engine = WorkflowEngine(approver=lambda *_: True)
        with self.assertRaises(ValueError):
            engine.run([])
        with self.assertRaises(ValueError):
            engine.run([WorkflowStep(str(i), "note") for i in range(21)])


if __name__ == "__main__":
    unittest.main()
