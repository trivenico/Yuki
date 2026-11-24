import os
import tempfile
import unittest
from pathlib import Path

from core.local_cli_runtime import build_local_conversation_runtime
from core.local_owner_identity import load_or_create_local_owner_id
from core.conversation_store import ConversationStore
from core.authorization import AuthorizationDecision, Principal
from core.authorization_gateway import AuthorizationGateway
from core.authorization_audit import SQLiteAuthorizationAuditLog
from core.production_security import ProductionSecurityContext




class AllowPolicy:
    def __init__(self):
        self.requests = []
    def authorize(self, request):
        self.requests.append(request)
        return AuthorizationDecision(True, 'decision-1', 'revision-1', 'allowed')


class MemoryAuditAnchor:
    def __init__(self):
        self.checkpoint = None
    def publish(self, checkpoint):
        self.checkpoint = checkpoint
    def latest(self):
        return self.checkpoint


def security_context(policy):
    audit_log = SQLiteAuthorizationAuditLog(str(Path(tempfile.mkdtemp()) / "authorization-audit.sqlite3"))
    anchor = MemoryAuditAnchor()
    audit_log.publish_checkpoint(anchor)
    return ProductionSecurityContext(
        authorization_gateway=AuthorizationGateway(policy, audit=audit_log),
        principal_for_actor=lambda owner: Principal(owner, 'human'),
        principal_for_worker=lambda worker: Principal(worker, 'workload', tenant_id='tenant-1', agent_id='worker-agent'),
        policy_revision='revision-1',
        audit_anchor=anchor,
    )

class FakeProvider:
    def __init__(self):
        self.calls = []

    def generate_chat(self, *, messages, tools, tool_choice):
        self.calls.append((messages, tools, tool_choice))
        return {"text": "Persistent answer", "tool_calls": {"tool_calls": []}}


class LocalOwnerIdentityTests(unittest.TestCase):
    def test_identity_is_stable_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            first = load_or_create_local_owner_id(directory)
            second = load_or_create_local_owner_id(directory)
            self.assertEqual(first, second)
            self.assertTrue(first.startswith("local-"))
            if os.name != "nt":
                mode = (Path(directory) / "local-owner-id").stat().st_mode & 0o777
                self.assertEqual(mode, 0o600)

    def test_invalid_existing_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "local-owner-id").write_text("not-an-owner\n", encoding="ascii")
            identity_path = Path(directory) / "local-owner-id"
            if os.name != "nt":
                identity_path.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError, "invalid"):
                load_or_create_local_owner_id(directory)


class LocalCliRuntimeTests(unittest.TestCase):
    def test_invalid_agent_step_limit_is_rejected_before_provider_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            initialized = []
            with self.assertRaisesRegex(ValueError, "max_agent_steps"):
                build_local_conversation_runtime(
                    {"max_agent_steps": True, "workspace": directory},
                    provider_factory=lambda config: initialized.append(True),
                    owner_id_factory=lambda: "local-test-owner-identity-000000000000",
                )
            self.assertEqual(initialized, [])

    def test_invalid_owner_identity_is_rejected_before_provider_initialization(self):
        initialized = []
        with self.assertRaisesRegex(ValueError, "owner identity"):
            build_local_conversation_runtime(
                {},
                provider_factory=lambda config: initialized.append(True),
                owner_id_factory=lambda: " ",
            )
        self.assertEqual(initialized, [])


    def test_strict_cli_requires_security_context_before_provider_initialization(self):
        initialized = []
        with self.assertRaisesRegex(ValueError, 'production_cli_requires_security_context'):
            build_local_conversation_runtime(
                {}, provider_factory=lambda config: initialized.append(True),
                owner_id_factory=lambda: 'local-test-owner-identity-000000000000',
                require_central_authorization=True,
            )
        self.assertEqual(initialized, [])

    def test_strict_cli_authorizes_provider_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider()
            policy = AllowPolicy()
            runtime = build_local_conversation_runtime(
                {'max_agent_steps': 3, 'workspace': directory},
                provider_factory=lambda config: provider,
                store_factory=lambda config: ConversationStore(Path(directory) / 'conversations.sqlite3'),
                owner_id_factory=lambda: 'local-test-owner-identity-000000000000',
                security=security_context(policy),
                require_central_authorization=True,
            )
            try:
                conversation = runtime.conversations.create(owner_id=runtime.owner_id)
                runtime.conversations.reply(
                    owner_id=runtime.owner_id,
                    conversation_id=conversation['conversation_id'],
                    user_text='Authorize this model call',
                    request_id='secure-cli-request',
                    expected_version=conversation['version'], tools=[],
                )
                self.assertEqual(len(policy.requests), 1)
                self.assertEqual(policy.requests[0].action, 'inference:invoke')
                self.assertEqual(policy.requests[0].principal.principal_id, runtime.owner_id)
            finally:
                runtime.close()

    def test_runtime_uses_guarded_agent_loop_and_persists_conversation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = FakeProvider()
            store_path = root / "conversations.sqlite3"
            runtime = build_local_conversation_runtime(
                {"max_agent_steps": 3},
                provider_factory=lambda config: provider,
                store_factory=lambda config: ConversationStore(store_path),
                owner_id_factory=lambda: "local-test-owner-identity-000000000000",
            )
            try:
                conversation = runtime.conversations.create(owner_id=runtime.owner_id)
                first = runtime.conversations.reply(
                    owner_id=runtime.owner_id,
                    conversation_id=conversation["conversation_id"],
                    user_text="Remember this conversation",
                    request_id="request-one",
                    expected_version=conversation["version"],
                    tools=[],
                )
                self.assertEqual(first["answer"], "Persistent answer")
                self.assertEqual(first["version"], 2)
                history = runtime.conversations.history(
                    owner_id=runtime.owner_id,
                    conversation_id=conversation["conversation_id"],
                )
                self.assertEqual([item["role"] for item in history], ["user", "assistant"])
                self.assertEqual(provider.calls[0][1], [])
            finally:
                runtime.close()

            reopened = ConversationStore(store_path)
            try:
                persisted = reopened.list_conversations(owner_id="local-test-owner-identity-000000000000")
                self.assertEqual(len(persisted), 1)
                self.assertEqual(persisted[0]["version"], 2)
            finally:
                pass


if __name__ == "__main__":
    unittest.main()
