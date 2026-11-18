import importlib.util
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if importlib.util.find_spec("PySide6"):
    from PySide6.QtWidgets import QApplication
    from ui.desktop.learning_center import LearningCenterPanel
else:
    QApplication = None
    LearningCenterPanel = None


@unittest.skipUnless(LearningCenterPanel is not None, "PySide6 desktop extra is not installed")
class LearningCenterPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.state = {
            "learning_consent": True,
            "training_consent": True,
            "pending": [{
                "proposal_id": "a" * 24,
                "task_type": "coding",
                "message_count": "2",
            }],
            "approved_count": 0,
        }
        self.reviewed = []

        def on_review(proposal_id):
            self.reviewed.append(proposal_id)
            return {
                "proposal_id": proposal_id,
                "messages": [
                    {"role": "user", "content": "Review this code."},
                    {"role": "assistant", "content": "Add a focused unit test."},
                ],
            }

        self.page = LearningCenterPanel.create(
            on_consent=lambda purpose, enabled: None,
            on_refresh=lambda: dict(self.state),
            on_review=on_review,
            on_approve=lambda proposal_id: None,
            on_reject=lambda proposal_id: None,
        )

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()

    def test_render_review_uses_plain_text_and_enables_approval_only_with_consent(self):
        controls = self.page.learning_controls
        controls["pending"].setCurrentRow(0)
        self.app.processEvents()
        self.assertEqual(self.reviewed, ["a" * 24])
        self.assertIn("USER:", controls["preview"].toPlainText())
        self.assertIn("Add a focused unit test.", controls["preview"].toPlainText())
        self.assertTrue(controls["approve"].isEnabled())
        self.assertTrue(controls["reject"].isEnabled())

    def test_training_consent_off_disables_approval(self):
        state = dict(self.state)
        state["training_consent"] = False
        self.page.learning_presenter.render_state(state)
        controls = self.page.learning_controls
        controls["pending"].setCurrentRow(0)
        self.app.processEvents()
        self.assertFalse(controls["approve"].isEnabled())
        self.assertTrue(controls["reject"].isEnabled())

    def test_pending_samples_are_hidden_when_learning_consent_is_off(self):
        state = dict(self.state)
        state["learning_consent"] = False
        with self.assertRaisesRegex(ValueError, "without learning consent"):
            self.page.learning_presenter.render_state(state)

    def test_preview_rejects_unbounded_or_malformed_message_content(self):
        with self.assertRaisesRegex(ValueError, "message content"):
            self.page.learning_presenter.render_review(
                "a" * 24,
                {
                    "proposal_id": "a" * 24,
                    "messages": [
                        {"role": "user", "content": "Question"},
                        {"role": "assistant", "content": "x" * 16_001},
                    ],
                },
            )


if __name__ == "__main__":
    unittest.main()
