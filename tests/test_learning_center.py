import importlib.util
import os
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if importlib.util.find_spec("PySide6"):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from ui.desktop.learning_center import LearningCenterPanel
else:
    QApplication = None
    LearningCenterPanel = None
    QEventLoop = None
    QTimer = None


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
        self.deleted = []
        self.callback_thread_ids = []
        self.main_thread_id = threading.get_ident()

        def on_refresh():
            self.callback_thread_ids.append(threading.get_ident())
            return dict(self.state)

        def on_review(proposal_id):
            self.callback_thread_ids.append(threading.get_ident())
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
            on_refresh=on_refresh,
            on_review=on_review,
            on_approve=lambda proposal_id: None,
            on_reject=lambda proposal_id: None,
            on_delete_all=lambda: self.deleted.append(True) or {"pending": 2, "approved": 3},
            on_submit=lambda messages, source, task: "b" * 24,
        )
        self.wait_for_learning_idle()

    def wait_for_learning_idle(self, timeout_ms=5000):
        elapsed = 0
        status = self.page.learning_controls["status"]
        while status.text() == "Working securely…" and elapsed < timeout_ms:
            QTest.qWait(10)
            elapsed += 10
        self.assertNotEqual(status.text(), "Working securely…")

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()

    def test_manual_submission_is_disabled_without_learning_consent(self):
        controls = self.page.learning_controls
        self.state["learning_consent"] = False
        controls["refresh"].click()
        self.wait_for_learning_idle()
        self.assertFalse(controls["submit"].isEnabled())
        self.state["learning_consent"] = True
        controls["refresh"].click()
        self.wait_for_learning_idle()
        self.assertTrue(controls["submit"].isEnabled())

    def test_storage_callbacks_run_off_the_gui_thread(self):
        self.assertTrue(self.callback_thread_ids)
        self.assertTrue(all(thread_id != self.main_thread_id for thread_id in self.callback_thread_ids))

    def test_render_review_uses_plain_text_and_enables_approval_only_with_consent(self):
        controls = self.page.learning_controls
        controls["pending"].setCurrentRow(0)
        self.wait_for_learning_idle()
        self.assertEqual(self.reviewed, ["a" * 24])
        self.assertIn("USER:", controls["preview"].toPlainText())
        self.assertIn("Add a focused unit test.", controls["preview"].toPlainText())
        self.assertTrue(controls["approve"].isEnabled())
        self.assertTrue(controls["reject"].isEnabled())
        self.assertTrue(controls["delete_all"].isEnabled())

    def test_delete_all_requires_confirmation_and_invokes_callback(self):
        from PySide6.QtWidgets import QMessageBox
        with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.Yes):
            self.page.learning_controls["delete_all"].click()
            self.app.processEvents()
        self.wait_for_learning_idle()
        self.assertEqual(self.deleted, [True])
        self.assertIn("Deleted 2 pending corrections and 3 approved examples", self.page.learning_controls["status"].text())

    def test_training_consent_off_disables_approval(self):
        state = dict(self.state)
        state["training_consent"] = False
        self.page.learning_presenter.render_state(state)
        controls = self.page.learning_controls
        controls["pending"].setCurrentRow(0)
        self.wait_for_learning_idle()
        self.assertFalse(controls["approve"].isEnabled())
        self.assertTrue(controls["reject"].isEnabled())

    def test_learning_consent_off_disables_approval_even_if_training_consent_is_on(self):
        state = dict(self.state)
        state["learning_consent"] = False
        state["pending"] = []
        state["retained_pending_count"] = 1
        self.page.learning_presenter.render_state(state)
        self.page.learning_presenter.render_review(
            "a" * 24,
            {
                "proposal_id": "a" * 24,
                "messages": [
                    {"role": "user", "content": "Review this code."},
                    {"role": "assistant", "content": "Add a focused unit test."},
                ],
            },
        )
        self.assertFalse(self.page.learning_controls["approve"].isEnabled())

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
