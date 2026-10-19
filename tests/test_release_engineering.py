import unittest

from yuki_version import __version__
from scripts.doctor import run_diagnostics


class ReleaseEngineeringTests(unittest.TestCase):
    def test_version_matches_0_2_0(self):
        self.assertEqual(__version__, "0.2.0")

    def test_run_diagnostics_executes_without_errors(self):
        ok, report = run_diagnostics()
        self.assertTrue(ok)
        self.assertIn("Yuki Environment & Capability Diagnostics", report)


if __name__ == "__main__":
    unittest.main()
