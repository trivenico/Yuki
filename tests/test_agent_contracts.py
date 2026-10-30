"""Tests for typed agent contracts and fail-closed delegation authorization."""
import unittest

from core.agent_contracts import AgentRegistry, AgentSpec, DelegationRequest


class AgentSpecTests(unittest.TestCase):
    def test_rejects_invalid_identity_and_bounds(self):
        with self.assertRaises(ValueError):
            AgentSpec("Bad ID", "research", "Research")
        with self.assertRaises(ValueError):
            AgentSpec("researcher", "research", "x" * 501)
        with self.assertRaises(ValueError):
            AgentSpec("researcher", "research", "Research", max_concurrency=0)
        with self.assertRaises(ValueError):
            AgentSpec("researcher", "research", "Research", allow_cloud=1)

    def test_normalizes_provider_preferences_and_freezes_capabilities(self):
        spec = AgentSpec(
            "researcher", "research", "Research",
            capabilities={"knowledge:read"},
            provider_preferences=(" OpenAI ", "ollama"),
        )
        self.assertEqual(spec.provider_preferences, ("openai", "ollama"))
        self.assertEqual(spec.capabilities, frozenset({"knowledge:read"}))

    def test_rejects_duplicate_or_malformed_capabilities(self):
        with self.assertRaises(ValueError):
            AgentSpec("researcher", "research", "Research", capabilities=("knowledge:read", "knowledge:read"))
        with self.assertRaises(ValueError):
            AgentSpec("researcher", "research", "Research", allowed_tools=("Run Shell",))


class DelegationTests(unittest.TestCase):
    def setUp(self):
        self.registry = AgentRegistry(max_agents=100)
        # Register the target first; delegation allow-lists may only reference
        # agents already present in the registry.
        self.registry.register(AgentSpec(
            "tester", "testing", "Creates test plans",
            capabilities={"test:design"},
        ))
        self.registry.register(AgentSpec(
            "coder", "coding", "Writes proposed code",
            capabilities={"agent:delegate", "code:review"},
            delegation_targets={"tester"},
        ))

    def test_authorizes_only_allowlisted_target_with_required_capabilities(self):
        request = DelegationRequest(
            "req-1", "task-1", "coder", "tester",
            "Create regression tests",
            required_capabilities={"test:design"},
            input_payload="Review this proposed patch.",
        )
        self.assertEqual(self.registry.authorize_delegation(request).agent_id, "tester")

    def test_rejects_unlisted_target(self):
        self.registry.register(AgentSpec("writer", "writing", "Writes docs"))
        request = DelegationRequest("req-2", "task-2", "coder", "writer", "Write docs")
        with self.assertRaises(PermissionError):
            self.registry.authorize_delegation(request)

    def test_rejects_missing_capability(self):
        request = DelegationRequest(
            "req-3", "task-3", "coder", "tester", "Do research",
            required_capabilities={"web:fetch"},
        )
        with self.assertRaises(PermissionError):
            self.registry.authorize_delegation(request)

    def test_rejects_non_delegating_source(self):
        self.registry.register(AgentSpec("observer", "research", "Read-only observer"))
        request = DelegationRequest("req-4", "task-4", "observer", "tester", "Delegate")
        with self.assertRaises(PermissionError):
            self.registry.authorize_delegation(request)

    def test_registry_capacity_and_duplicate_ids(self):
        tiny = AgentRegistry(max_agents=1)
        tiny.register(AgentSpec("first", "research", "First"))
        with self.assertRaises(ValueError):
            tiny.register(AgentSpec("first", "research", "Duplicate"))
        with self.assertRaises(ValueError):
            tiny.register(AgentSpec("second", "research", "Over capacity"))

    def test_rejects_unregistered_delegation_targets_at_registration(self):
        with self.assertRaises(ValueError):
            AgentRegistry().register(AgentSpec(
                "parent", "planner", "Planner",
                capabilities={"agent:delegate"},
                delegation_targets={"missing"},
            ))


if __name__ == "__main__":
    unittest.main()
