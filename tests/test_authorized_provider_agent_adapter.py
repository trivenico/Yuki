import unittest

from core.authorization import AuthorizationDecision, Principal
from core.authorization_gateway import AuthorizationGateway
from providers.authorized_agent_adapter import AuthorizedProviderAgentAdapter


class Policy:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.requests = []

    def authorize(self, request):
        self.requests.append(request)
        return AuthorizationDecision(
            self.allowed, "decision-1", "revision-1",
            "allowed" if self.allowed else "denied",
        )


class FakeProvider:
    model = "test-model"

    def __init__(self):
        self.calls = 0
        self.stream_calls = 0

    def generate_chat(self, *, messages, tools, tool_choice):
        self.calls += 1
        return {"text": "ok", "tool_calls": {"tool_calls": []}}

    def generate_chat_stream(self, *, messages, tools, tool_choice):
        self.stream_calls += 1
        yield {"type": "text_delta", "text": "ok"}


class AuthorizedProviderAgentAdapterTests(unittest.TestCase):
    def setUp(self):
        self.principal = Principal(
            principal_id="owner-1", kind="human", tenant_id="tenant-1",
            permissions=frozenset({"inference:invoke"}),
        )
        self.policy = Policy()
        self.audit = []
        self.gateway = AuthorizationGateway(
            self.policy, audit=lambda request, decision: self.audit.append((request, decision))
        )
        self.provider = FakeProvider()
        self.adapter = AuthorizedProviderAgentAdapter(
            self.provider, authorization_gateway=self.gateway,
            principal=self.principal, provider_name="test-provider",
            task_id="task-1", delegation_id="delegation-1",
            approval_id="approval-1", policy_revision="revision-1",
        )
        self.messages = [{"role": "user", "content": "private prompt"}]

    def test_non_streaming_call_is_authorized_and_context_bound(self):
        result = self.adapter(messages=self.messages, tools=[], tool_choice="auto")
        self.assertEqual(result["text"], "ok")
        request = self.policy.requests[0]
        self.assertEqual(request.action, "inference:invoke")
        self.assertEqual(request.principal, self.principal)
        self.assertEqual(request.task_id, "task-1")
        self.assertEqual(request.delegation_id, "delegation-1")
        self.assertEqual(request.approval_id, "approval-1")
        self.assertEqual(request.expected_policy_revision, "revision-1")
        self.assertNotIn("private prompt", request.resource)
        self.assertEqual(len(self.audit), 1)

    def test_denial_blocks_provider_network_boundary(self):
        self.policy.allowed = False
        with self.assertRaises(PermissionError):
            self.adapter(messages=self.messages, tools=[])
        self.assertEqual(self.provider.calls, 0)

    def test_audit_failure_blocks_provider_boundary(self):
        adapter = AuthorizedProviderAgentAdapter(
            self.provider,
            authorization_gateway=AuthorizationGateway(
                self.policy, audit=lambda request, decision: False
            ),
            principal=self.principal,
        )
        with self.assertRaisesRegex(PermissionError, "audit_unavailable"):
            adapter(messages=self.messages, tools=[])
        self.assertEqual(self.provider.calls, 0)

    def test_stream_is_authorized_before_provider_stream_is_opened(self):
        self.policy.allowed = False
        with self.assertRaises(PermissionError):
            self.adapter.stream_model(messages=self.messages, tools=[])
        self.assertEqual(self.provider.stream_calls, 0)

    def test_stream_authorization_occurs_before_stream_iteration(self):
        events = self.adapter.stream_model(messages=self.messages, tools=[])
        self.assertEqual(len(self.policy.requests), 1)
        self.assertEqual(self.provider.stream_calls, 0)
        self.assertEqual(next(iter(events))["text"], "ok")
        self.assertEqual(self.provider.stream_calls, 1)

    def test_provider_model_identity_change_blocks_the_request(self):
        self.provider.model = "different-model"
        with self.assertRaisesRegex(RuntimeError, "provider_model_identity_changed"):
            self.adapter(messages=self.messages, tools=[])
        self.assertEqual(self.provider.calls, 0)

    def test_fingerprint_changes_with_prompt(self):
        self.adapter._authorize_invocation(self.messages, [], "auto")
        first_resource = self.policy.requests[-1].resource
        self.adapter._authorize_invocation(
            [{"role": "user", "content": "different prompt"}], [], "auto"
        )
        self.assertNotEqual(first_resource, self.policy.requests[-1].resource)

    def test_requires_trusted_gateway_and_principal(self):
        with self.assertRaises(TypeError):
            AuthorizedProviderAgentAdapter(
                self.provider, authorization_gateway=object(),
                principal=self.principal,
            )
        with self.assertRaises(TypeError):
            AuthorizedProviderAgentAdapter(
                self.provider, authorization_gateway=self.gateway,
                principal="owner-1",
            )


if __name__ == "__main__":
    unittest.main()
