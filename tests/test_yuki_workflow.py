import unittest

from yuki_workflow import (
    Stage,
    create_workflow,
    transition,
    workflow_summary,
)


class WorkflowTests(unittest.TestCase):
    def test_happy_path_stages_are_explicit(self):
        workflow = create_workflow("Organize project files")
        self.assertEqual(workflow.stage, Stage.WAKE)
        for stage in (
            Stage.PERCEIVE,
            Stage.UNDERSTAND,
            Stage.PLAN,
            Stage.CHECK_TOOLS,
            Stage.EXECUTE,
            Stage.OBSERVE,
            Stage.FEEDBACK,
            Stage.REPORT,
            Stage.COMPLETE,
        ):
            transition(workflow, stage)
        self.assertEqual(workflow.stage, Stage.COMPLETE)
        self.assertIn("no tools or external actions have been executed", workflow_summary(workflow))

    def test_sensitive_action_can_be_gated_by_permission_stage(self):
        workflow = create_workflow("Perform an active security test")
        for stage in (Stage.PERCEIVE, Stage.UNDERSTAND, Stage.PLAN, Stage.CHECK_TOOLS, Stage.ASK_PERMISSION):
            transition(workflow, stage)
        self.assertEqual(workflow.stage, Stage.ASK_PERMISSION)
        transition(workflow, Stage.BLOCKED, "Approval was not granted")
        self.assertEqual(workflow.stage, Stage.BLOCKED)

    def test_illegal_transition_is_rejected(self):
        workflow = create_workflow("Test")
        with self.assertRaises(ValueError):
            transition(workflow, Stage.EXECUTE)

    def test_terminal_state_cannot_restart(self):
        workflow = create_workflow("Test")
        transition(workflow, Stage.CANCELLED)
        with self.assertRaises(ValueError):
            transition(workflow, Stage.PERCEIVE)


if __name__ == "__main__":
    unittest.main()
