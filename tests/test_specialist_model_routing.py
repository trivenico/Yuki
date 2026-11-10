import unittest
from unittest.mock import Mock

from automation.specialist_worker import build_handlers_from_provider_manager


class _Provider:
    def generate(self, prompt):
        return "specialist result"

    def is_available(self):
        return True


class SpecialistModelRoutingTests(unittest.TestCase):
    def test_openai_and_ollama_specialists_use_exact_model_router(self):
        manager = Mock()
        manager.create_selected_adapter.return_value = _Provider()
        manager.create_routed_model_adapter.return_value = _Provider()
        manager.create_routed_adapter.return_value = _Provider()
        manager.current.return_value.name = "ollama"

        handlers = build_handlers_from_provider_manager(
            manager,
            {"coding": "coding"},
            provider_preferences={"coding": ("ollama",)},
        )
        self.assertEqual(handlers["coding"]("implement a feature"), "specialist result")
        manager.create_routed_model_adapter.assert_called_once_with(
            required=("text",), provider_preference=("ollama",), allow_cloud=False,
        )
        manager.create_routed_adapter.assert_not_called()

    def test_selected_exact_model_provider_uses_model_router(self):
        manager = Mock()
        manager.create_selected_adapter.return_value = _Provider()
        manager.create_routed_model_adapter.return_value = _Provider()
        manager.create_routed_adapter.return_value = _Provider()
        manager.current.return_value.name = "openai"

        handlers = build_handlers_from_provider_manager(manager, {"research": "research"})
        self.assertEqual(handlers["research"]("investigate"), "specialist result")
        manager.create_routed_model_adapter.assert_called_once_with(
            required=("text",), provider_preference=("openai",), allow_cloud=False,
        )
        manager.create_routed_adapter.assert_not_called()

    def test_gemini_keeps_explicit_adapter_level_route_until_pinnable(self):
        manager = Mock()
        manager.create_selected_adapter.return_value = _Provider()
        manager.create_routed_model_adapter.return_value = _Provider()
        manager.create_routed_adapter.return_value = _Provider()
        manager.current.return_value.name = "gemini"

        handlers = build_handlers_from_provider_manager(manager, {"research": "research"})
        self.assertEqual(handlers["research"]("investigate"), "specialist result")
        manager.create_routed_adapter.assert_called_once_with(
            required=("text",), preference=("gemini",), allow_cloud=False,
        )
        manager.create_routed_model_adapter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
