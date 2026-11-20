import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from defense.firewall import FirewallError, FirewallManager


class FirewallManagerTests(unittest.TestCase):
    def test_disabled_by_default(self):
        manager = FirewallManager(approver=lambda prompt: True)
        with self.assertRaisesRegex(FirewallError, "disabled"):
            manager.block_temporary("203.0.113.10")

    def test_only_dry_run_mode_is_accepted(self):
        with self.assertRaisesRegex(FirewallError, "Only dry-run"):
            FirewallManager(enabled=True, approver=lambda prompt: True, dry_run=False)

    def test_approved_plan_is_never_applied(self):
        prompts = []
        manager = FirewallManager(
            enabled=True, approver=lambda prompt: prompts.append(prompt) or True,
        )
        result = manager.block_temporary("203.0.113.10", seconds=60)
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["expires_in_seconds"], 60)
        self.assertEqual(len(prompts), 1)
        self.assertIn("dry-run plan", prompts[0])

    def test_approval_must_be_exact_true(self):
        manager = FirewallManager(enabled=True, approver=lambda prompt: 1)
        with self.assertRaisesRegex(FirewallError, "approval"):
            manager.block_temporary("203.0.113.10")

    def test_approval_callback_failure_is_sanitized(self):
        def broken(prompt):
            raise RuntimeError("/private/path")
        manager = FirewallManager(enabled=True, approver=broken)
        with self.assertRaisesRegex(FirewallError, "could not be verified") as caught:
            manager.block_temporary("203.0.113.10")
        self.assertNotIn("/private/path", str(caught.exception))

    def test_plan_count_is_bounded_and_removable(self):
        manager = FirewallManager(
            enabled=True, approver=lambda prompt: True, max_rules=1,
        )
        result = manager.block_temporary("203.0.113.10")
        with self.assertRaisesRegex(FirewallError, "limit"):
            manager.block_temporary("203.0.113.11")
        self.assertTrue(manager.remove(result["rule_id"]))
        self.assertFalse(manager.remove(result["rule_id"]))


    def test_concurrent_approvals_cannot_exceed_plan_limit(self):
        barrier = Barrier(2)
        def approve(prompt):
            barrier.wait(timeout=2)
            return True
        manager = FirewallManager(enabled=True, approver=approve, max_rules=1)

        def create(address):
            try:
                manager.block_temporary(address)
                return "created"
            except FirewallError:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, ("203.0.113.10", "203.0.113.11")))
        self.assertCountEqual(results, ["created", "rejected"])
        self.assertEqual(len(manager._rules), 1)

    def test_rejects_special_and_malformed_addresses(self):
        manager = FirewallManager(enabled=True, approver=lambda prompt: True)
        for address in ("127.0.0.1", "::1", "224.0.0.1", "0.0.0.0",
                        "169.254.1.1", "not-an-ip", "", "x" * 65):
            with self.subTest(address=address):
                with self.assertRaises(FirewallError):
                    manager.block_temporary(address)

    def test_rejects_invalid_configuration_and_duration(self):
        with self.assertRaises(ValueError):
            FirewallManager(enabled=1)
        with self.assertRaises(ValueError):
            FirewallManager(max_rules=0)
        manager = FirewallManager(enabled=True, approver=lambda prompt: True)
        for seconds in (True, 29, 3601, 60.0):
            with self.subTest(seconds=seconds):
                with self.assertRaises(FirewallError):
                    manager.block_temporary("203.0.113.10", seconds=seconds)


if __name__ == "__main__":
    unittest.main()
