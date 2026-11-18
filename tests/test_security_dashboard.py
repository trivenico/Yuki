import os
import unittest
from importlib.util import find_spec
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if find_spec("PySide6") is not None:
    from PySide6.QtWidgets import QApplication, QMessageBox
    from ui.desktop.security_dashboard import SecurityDashboardPanel
else:
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is an optional desktop dependency")
class SecurityDashboardPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self):
        calls = []
        page = SecurityDashboardPanel.create(
            on_start=lambda: calls.append("start"),
            on_stop=lambda: calls.append("stop"),
            on_refresh=lambda: calls.append("refresh"),
        )
        return page, calls

    def test_construction_is_passive_and_controls_start_disabled(self):
        page, calls = self.make_panel()
        controls = page.security_controls
        self.assertFalse(controls["stop"].isEnabled())
        self.assertFalse(controls["refresh"].isEnabled())
        self.assertTrue(controls["start"].isEnabled())
        self.assertEqual(calls, [])
        page.close()

    def test_start_requires_explicit_confirmation(self):
        page, calls = self.make_panel()
        controls = page.security_controls
        with patch("PySide6.QtWidgets.QMessageBox.question",
                   return_value=QMessageBox.No):
            controls["start"].click()
        self.assertEqual(calls, [])
        with patch("PySide6.QtWidgets.QMessageBox.question",
                   return_value=QMessageBox.Yes):
            controls["start"].click()
        self.assertEqual(calls, ["start"])
        page.close()

    def test_render_requires_read_only_snapshot_and_uses_fixed_alert_text(self):
        page, _calls = self.make_panel()
        presenter = page.security_presenter
        with self.assertRaisesRegex(ValueError, "read-only"):
            presenter.render({"processes": [], "alerts": []})
        presenter.render({
            "enabled": True,
            "read_only": True,
            "persisted": False,
            "automated_response": False,
            "process_count": 1,
            "processes": [{
                "pid": 7,
                "name": "<script>bad()</script>",
                "status": "running",
            }],
            "alerts": [{
                "type": "possible_port_scan",
                "message": "<script>attacker-controlled</script>",
            }],
        })
        controls = page.security_controls
        self.assertEqual(controls["processes"].count(), 1)
        self.assertIn("<script>", controls["processes"].item(0).text())
        self.assertEqual(
            controls["alerts"].item(0).text(),
            "Possible port scan detected; verify network activity.",
        )
        self.assertNotIn("attacker-controlled", controls["alerts"].item(0).text())
        page.close()

    def test_state_controls_refresh_and_stop(self):
        page, calls = self.make_panel()
        controls = page.security_controls
        page.security_presenter.set_state("Listening", enabled=True)
        self.assertTrue(controls["stop"].isEnabled())
        self.assertTrue(controls["refresh"].isEnabled())
        self.assertFalse(controls["start"].isEnabled())
        controls["refresh"].click()
        controls["stop"].click()
        self.assertEqual(calls, ["refresh", "stop"])
        page.close()

    def test_render_rejects_oversized_snapshot_before_mutating_widgets(self):
        page, _calls = self.make_panel()
        presenter = page.security_presenter
        controls = page.security_controls
        with self.assertRaisesRegex(ValueError, "limits"):
            presenter.render({
                "enabled": True,
                "read_only": True,
                "persisted": False,
                "automated_response": False,
                "processes": [{}] * 5001,
                "alerts": [],
            })
        self.assertEqual(controls["processes"].count(), 0)
        page.close()

    def test_render_discloses_process_inventory_truncation(self):
        page, _calls = self.make_panel()
        page.security_presenter.render({
            "enabled": True,
            "read_only": True,
            "persisted": False,
            "automated_response": False,
            "process_count": 2048,
            "process_truncated": True,
            "processes": [{"pid": 1, "name": "example", "status": "running"}],
            "alerts": [],
        })
        summary = page.security_controls["process_summary"].text()
        self.assertIn("capped", summary)
        self.assertIn("not inspected", summary)
        page.close()

    def test_disabling_monitoring_clears_stale_snapshot_data(self):
        page, _calls = self.make_panel()
        presenter = page.security_presenter
        presenter.render({
            "enabled": True,
            "read_only": True,
            "persisted": False,
            "automated_response": False,
            "processes": [{"pid": 1, "name": "example", "status": "running"}],
            "alerts": [{"type": "possible_port_scan"}],
        })
        controls = page.security_controls
        self.assertEqual(controls["processes"].count(), 1)
        self.assertEqual(controls["alerts"].count(), 1)
        presenter.set_state("off", enabled=False)
        self.assertEqual(controls["processes"].count(), 0)
        self.assertEqual(controls["alerts"].count(), 0)
        self.assertEqual(controls["process_summary"].text(), "No snapshot collected.")
        page.close()


if __name__ == "__main__":
    unittest.main()
