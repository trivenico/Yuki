import os
import unittest

from core.env_guard import sanitize_env
from core.permissions import PermissionGate
from core.skills_guard import scan_source


class SecurityHardeningTests(unittest.TestCase):
    def test_env_guard_strips_keys_and_secrets(self):
        raw_env = {
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "sk-secret123",
            "YUKI_CLOUD_API_KEY_ENV": "OPENAI_API_KEY",
            "DATABASE_PASSWORD": "pass",
            "USER_TOKEN": "token_abc",
            "NORMAL_VAR": "hello",
        }
        clean = sanitize_env(raw_env)
        self.assertIn("PATH", clean)
        self.assertIn("NORMAL_VAR", clean)
        self.assertNotIn("OPENAI_API_KEY", clean)
        self.assertNotIn("DATABASE_PASSWORD", clean)
        self.assertNotIn("USER_TOKEN", clean)

    def test_skills_guard_flags_dangerous_patterns(self):
        dangerous_code = "import os\nos.system('rm -rf /')\neval('2+2')"
        findings = scan_source(dangerous_code)
        self.assertEqual(len(findings), 2)
        reasons = [f["finding"] for f in findings]
        self.assertTrue(any("os.system" in r for r in reasons))
        self.assertTrue(any("eval" in r for r in reasons))

    def test_skills_guard_accepts_safe_code(self):
        safe_code = "def add(a, b):\n    return a + b\n"
        findings = scan_source(safe_code)
        self.assertEqual(len(findings), 0)

    def test_permission_gate_rate_limiting(self):
        gate = PermissionGate(approver=lambda req: True, max_rate_limit=3, rate_window_seconds=10)
        self.assertTrue(gate.request("action1", "reason1"))
        self.assertTrue(gate.request("action2", "reason2"))
        self.assertTrue(gate.request("action3", "reason3"))
        # 4th request exceeds rate limit -> auto-deny
        self.assertFalse(gate.request("action4", "reason4"))


if __name__ == "__main__":
    unittest.main()
