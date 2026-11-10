import unittest

from automation.specialist_provider_wiring import (
    SpecialistProviderWiringError,
    build_catalog_provider_handlers,
)


class FakeProvider:
    def is_available(self):
        return True
    def generate(self, prompt):
        return "bounded specialist result"


class FakeManager:
    def __init__(self):
        self.selected_calls = 0
        self.routed_calls = []
    def create_selected_adapter(self):
        self.selected_calls += 1
        return FakeProvider()
    def create_routed_adapter(self, **kwargs):
        self.routed_calls.append(kwargs)
        return FakeProvider()


class SpecialistProviderWiringTests(unittest.TestCase):
    def test_explicit_catalog_roles_create_handlers(self):
        manager = FakeManager()
        handlers = build_catalog_provider_handlers(
            manager, role_map={"research": "research", "engineering": "coding"}
        )
        self.assertEqual(set(handlers), {"research", "engineering"})
        self.assertEqual(handlers["research"]("Investigate this"), "bounded specialist result")
        self.assertEqual(manager.selected_calls, 1)

    def test_unknown_specialist_is_rejected(self):
        with self.assertRaises(SpecialistProviderWiringError):
            build_catalog_provider_handlers(FakeManager(), role_map={"unknown": "research"})

    def test_unsupported_role_is_rejected(self):
        with self.assertRaises(SpecialistProviderWiringError):
            build_catalog_provider_handlers(FakeManager(), role_map={"research": "security"})

    def test_cloud_routing_requires_explicit_opt_in(self):
        manager = FakeManager()
        build_catalog_provider_handlers(
            manager, role_map={"research": "research"},
            provider_preferences={"research": ("cloud-provider",)},
        )
        self.assertEqual(manager.routed_calls[0]["allow_cloud"], False)

    def test_cloud_routing_opt_in_is_passed_explicitly(self):
        manager = FakeManager()
        build_catalog_provider_handlers(
            manager, role_map={"research": "research"},
            provider_preferences={"research": ("cloud-provider",)},
            allow_cloud=True,
        )
        self.assertEqual(manager.routed_calls[0]["allow_cloud"], True)

    def test_empty_or_oversized_mapping_rejected(self):
        with self.assertRaises(SpecialistProviderWiringError):
            build_catalog_provider_handlers(FakeManager(), role_map={})
        with self.assertRaises(SpecialistProviderWiringError):
            build_catalog_provider_handlers(
                FakeManager(),
                role_map={f"agent-{i}": "research" for i in range(13)},
            )


if __name__ == "__main__":
    unittest.main()
