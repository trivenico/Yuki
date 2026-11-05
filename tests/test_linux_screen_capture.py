import json
import os
from pathlib import Path
import tempfile
import unittest

from tools.yuki_screen_capture import CaptureError, capture_png


class LinuxScreenCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.grants = self.runtime / "yuki-screen-grants"
        self.grants.mkdir(mode=0o700)
        self.session = "session_123"
        self.grant_file = self.grants / (self.session + ".json")
        self.grant_file.write_text(json.dumps({
            "enabled": True, "session_id": self.session,
            "expires_at_epoch": 500,
        }))
        self.grant_file.chmod(0o600)
        self.png = (
            b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
            + (10).to_bytes(4, "big") + (20).to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00" + b"\x00\x00\x00\x00"
        )

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, output=None, returncode=0):
        def run(command, **kwargs):
            self.assertEqual(kwargs["timeout"], 12)
            self.assertEqual(kwargs["stdin"], -3)
            return type("Result", (), {
                "stdout": self.png if output is None else output,
                "returncode": returncode,
            })()
        return run

    def test_captures_wayland_using_grim_without_shell(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return type("Result", (), {"stdout": self.png, "returncode": 0})()
        output = capture_png(
            session_id=self.session, now_epoch=100,
            env={"WAYLAND_DISPLAY": "wayland-0"}, runner=runner,
            runtime_dir=str(self.runtime),
        )
        self.assertEqual(output, self.png)
        self.assertEqual(calls, [["/usr/bin/grim", "-"]])

    def test_captures_x11_using_imagemagick_import(self):
        output = capture_png(
            session_id=self.session, now_epoch=100,
            env={"DISPLAY": ":0"}, runner=self.runner(),
            runtime_dir=str(self.runtime),
        )
        self.assertEqual(output, self.png)

    def test_requires_local_short_lived_consent_file(self):
        self.grant_file.unlink()
        with self.assertRaises(CaptureError):
            capture_png(session_id=self.session, now_epoch=100,
                        env={"DISPLAY": ":0"}, runner=self.runner(),
                        runtime_dir=str(self.runtime))

    def test_rejects_expired_or_wrong_session_grant(self):
        data = json.loads(self.grant_file.read_text())
        data["expires_at_epoch"] = 99
        self.grant_file.write_text(json.dumps(data))
        self.grant_file.chmod(0o600)
        with self.assertRaises(CaptureError):
            capture_png(session_id=self.session, now_epoch=100,
                        env={"DISPLAY": ":0"}, runner=self.runner(),
                        runtime_dir=str(self.runtime))

    def test_rejects_grant_with_permissive_permissions(self):
        self.grant_file.chmod(0o644)
        with self.assertRaises(CaptureError):
            capture_png(session_id=self.session, now_epoch=100,
                        env={"DISPLAY": ":0"}, runner=self.runner(),
                        runtime_dir=str(self.runtime))

    def test_rejects_invalid_session_id_and_missing_backend(self):
        with self.assertRaises(CaptureError):
            capture_png(session_id="../../etc/passwd", now_epoch=100,
                        env={"DISPLAY": ":0"}, runner=self.runner(),
                        runtime_dir=str(self.runtime))
        with self.assertRaises(CaptureError):
            capture_png(session_id=self.session, now_epoch=100,
                        env={}, runner=self.runner(),
                        runtime_dir=str(self.runtime))

    def test_rejects_invalid_or_oversized_capture(self):
        for payload in (b"not png", b"\x89PNG\r\n\x1a\n" + b"x" * (9 * 1024 * 1024)):
            with self.assertRaises(CaptureError):
                capture_png(session_id=self.session, now_epoch=100,
                            env={"DISPLAY": ":0"}, runner=self.runner(payload),
                            runtime_dir=str(self.runtime))


if __name__ == "__main__":
    unittest.main()
