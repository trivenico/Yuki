"""Firewall identity checks for local specialist worker composition."""
import tempfile
import unittest

from automation.local_worker_runtime import build_local_worker_runtime


class FakeProvider:
    def is_available(self):
        return True

    def generate(self, prompt):
        return "bounded output"


class FakeProviderManager:
    def __init__(self, policy):
        self.firewall_policy = policy

    def create_selected_adapter(self):
        return FakeProvider()

    def create_routed_adapter(self, *, required, preference, allow_cloud):
        return FakeProvider()


class LocalWorkerFirewallBindingTests(unittest.TestCase):
    def test_worker_runtime_requires_exact_shared_policy(self):
        policy = object()
        manager = FakeProviderManager(policy)
        with tempfile.TemporaryDirectory() as directory:
            built = build_local_worker_runtime(
                directory, provider_manager=manager, firewall_policy=policy,
            )
        self.assertEqual(
            set(built.supervisor.worker.handlers),
            {"research", "coding", "testing", "reporting"},
        )

    def test_worker_runtime_rejects_different_policy_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "exact local worker firewall policy"):
                build_local_worker_runtime(
                    directory,
                    provider_manager=FakeProviderManager(object()),
                    firewall_policy=object(),
                )

    def test_worker_runtime_does_not_accept_policy_without_provider_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "requires a provider_manager"):
                build_local_worker_runtime(
                    directory, FakeProvider(), firewall_policy=object(),
                )


if __name__ == "__main__":
    unittest.main()
