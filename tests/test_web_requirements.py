"""Regression tests for optional browser-runtime installation metadata."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WebRequirementsTests(unittest.TestCase):
    def test_web_requirements_are_real_lines(self):
        requirements = (ROOT / "requirements-web.txt").read_text(encoding="utf-8")
        lines = [line.strip() for line in requirements.splitlines() if line.strip()]
        self.assertFalse(any(r"\\n" in line or r"\\r" in line for line in lines))
        self.assertEqual(
            lines,
            [
                "# Optional ASGI web server for Yuki's browser interface.",
                "uvicorn[standard]>=0.30.0,<1.0",
            ],
        )


if __name__ == "__main__":
    unittest.main()
