import unittest

from core.awareness import AwarenessSnapshot, render_awareness


class AwarenessTests(unittest.TestCase):
    def test_renders_bounded_foreground_and_background_state(self):
        snapshot = AwarenessSnapshot(
            mode="background",
            current_goal="Review approved task outcomes",
            known_context=("Task 42 completed and verified.",),
            open_questions=("Does the owner want this lesson saved?",),
            recent_events=("Task completed.",),
            uncertainty="medium",
        )
        rendered = render_awareness(snapshot)
        self.assertIn("Mode: background", rendered)
        self.assertIn("not permission or policy", rendered)
        self.assertIn("Does the owner want this lesson saved?", rendered)

    def test_rejects_unbounded_or_invalid_state(self):
        with self.assertRaises(ValueError):
            render_awareness(AwarenessSnapshot("unknown", "goal"))
        with self.assertRaises(ValueError):
            render_awareness(AwarenessSnapshot("foreground", "goal", known_context=("x",) * 9))
        with self.assertRaises(ValueError):
            render_awareness(AwarenessSnapshot("foreground", "goal", uncertainty="certain"))


if __name__ == "__main__":
    unittest.main()
