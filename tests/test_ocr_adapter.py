import subprocess
import unittest

from providers.ocr_adapter import OCRProcessingError, extract_screen_text
from providers.screen_observation_policy import ScreenObservationGrant


class OCRAdapterTests(unittest.TestCase):
    def setUp(self):
        self.grant = ScreenObservationGrant(
            grant_id="g", owner_id="owner", device_id="device",
            session_id="session", issued_at_epoch=1, expires_at_epoch=100,
            allowed_transports=("local", "ssh"),
        )
        self.png = (
            b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
            + (20).to_bytes(4, "big") + (10).to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00" + b"\x00\x00\x00\x00"
        )

    def call(self, **kwargs):
        options = {
            "grant": self.grant, "owner_id": "owner",
            "device_id": "device", "session_id": "session",
            "transport": "local", "now_epoch": 10,
            "executable_lookup": lambda name: "/usr/bin/tesseract",
        }
        options.update(kwargs)
        return extract_screen_text(self.png, **options)

    def test_extracts_local_ocr_text(self):
        seen = {}
        def runner(command, **options):
            seen["command"] = command
            seen["options"] = options
            return type("Result", (), {"returncode": 0, "stdout": b"Build failed"})()
        result = self.call(runner=runner)
        self.assertEqual(result.text, "Build failed")
        self.assertEqual(result.engine, "tesseract")
        self.assertEqual(result.image_width, 20)
        self.assertEqual(seen["command"][:3], ["/usr/bin/tesseract", "stdin", "stdout"])
        self.assertFalse(seen["options"]["shell"])
        self.assertEqual(seen["options"]["input"], self.png)

    def test_requires_active_screen_consent(self):
        with self.assertRaises(PermissionError):
            extract_screen_text(
                self.png, grant=self.grant, owner_id="owner",
                device_id="wrong", session_id="session",
                transport="local", now_epoch=10,
                executable_lookup=lambda name: "/usr/bin/tesseract",
                runner=lambda *a, **k: None,
            )

    def test_rejects_missing_engine(self):
        with self.assertRaises(OCRProcessingError):
            self.call(executable_lookup=lambda name: None)

    def test_rejects_invalid_language_and_timeout(self):
        with self.assertRaises(OCRProcessingError):
            self.call(language="eng;touch")
        with self.assertRaises(OCRProcessingError):
            self.call(timeout_seconds=21)

    def test_timeout_is_bounded(self):
        def runner(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        with self.assertRaises(OCRProcessingError):
            self.call(runner=runner)

    def test_rejects_engine_failure_and_oversized_output(self):
        def failed(*args, **kwargs):
            return type("Result", (), {"returncode": 1, "stdout": b""})()
        with self.assertRaises(OCRProcessingError):
            self.call(runner=failed)
        def oversized(*args, **kwargs):
            return type("Result", (), {
                "returncode": 0, "stdout": b"x" * (64 * 1024 + 1)
            })()
        with self.assertRaises(OCRProcessingError):
            self.call(runner=oversized)


if __name__ == "__main__":
    unittest.main()
