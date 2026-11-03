import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path

from core.auth import BuiltInAuthService
from core.skill_execution_manifest import validate_manifest
from core.skill_invocation_approval import (
    InvocationApprovalError,
    OneShotInvocationApprovalStore,
    build_invocation_binding,
)


class SkillInvocationApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = BuiltInAuthService(
            str(Path(self.temp.name) / "auth.db"), password_iterations=100_000
        )
        self.auth.bootstrap_admin("operator.one", "another-correct-horse-battery")
        self.auth.create_account("user.one", "correct-horse-battery-staple")
        self.auth.create_account("user.two", "correct-horse-battery-staple")
        self.actor_session = self.auth.authenticate(
            "user.one", "correct-horse-battery-staple"
        ).token
        self.other_actor_session = self.auth.authenticate(
            "user.two", "correct-horse-battery-staple"
        ).token
        self.approver_session = self.auth.authenticate(
            "operator.one", "another-correct-horse-battery"
        ).token
        self.store = OneShotInvocationApprovalStore(
            Path(self.temp.name) / "approvals.db"
        )
        self.manifest = validate_manifest({
            "protocol_version": 1,
            "skill_id": "report_builder",
            "skill_version": "1.0.0",
            "artifact_sha256": "a" * 64,
            "entrypoint": "package.entry:run",
            "input_schema_sha256": "b" * 64,
            "output_schema_sha256": "c" * 64,
            "runtime_image": "registry.example/yuki-runtime@sha256:" + "d" * 64,
            "environment_lock_sha256": "e" * 64,
            "capabilities": ["read_staged_input", "write_staged_output"],
            "resource_limits": {
                "timeout_seconds": 30, "memory_mb": 256, "cpu_millis": 500,
                "max_processes": 16, "max_input_bytes": 65536,
                "max_output_bytes": 262144,
            },
            "network": "none",
            "mount_policy": "invocation_staging_only",
        })
        self.kwargs = {
            "auth_service": self.auth,
            "actor_session_token": self.actor_session,
            "tenant_id": "tenant-1",
            "task_id": "task-1",
            "invocation_id": "invocation-1",
            "arguments": {"report": "monthly", "count": 3},
            "staging_sha256": hashlib.sha256(b"stage").hexdigest(),
            "read_grants": ("input/report.json",),
            "write_grants": ("output/report.json",),
        }

    def binding(self, **overrides):
        args = dict(self.kwargs)
        args.update(overrides)
        return build_invocation_binding(self.manifest, **args)

    def consume(self, approval, binding, **overrides):
        args = {
            "auth_service": self.auth,
            "actor_session_token": self.actor_session,
            "current_approval_revision": "revision-1",
        }
        args.update(overrides)
        return self.store.consume(approval, binding, **args)

    def issue(self, binding=None, **overrides):
        args = {
            "auth_service": self.auth,
            "approver_session_token": self.approver_session,
            "approval_revision": "revision-1",
            "ttl_seconds": 300,
        }
        args.update(overrides)
        return self.store.issue(binding or self.binding(), **args)

    def test_forged_or_missing_actor_session_is_rejected(self):
        for token in ("forged-session-token", "", None):
            with self.subTest(token=token), self.assertRaises(InvocationApprovalError):
                self.binding(actor_session_token=token)

    def test_exact_binding_is_consumed_once(self):
        binding = self.binding()
        approval = self.issue(binding)
        self.assertTrue(self.store.consume(
            approval, binding, current_approval_revision="revision-1"
        ))
        self.assertFalse(self.store.consume(
            approval, binding, current_approval_revision="revision-1"
        ))

    def test_actor_tenant_task_invocation_arguments_and_staging_are_bound(self):
        binding = self.binding()
        approval = self.issue(binding)
        changes = (
            self.binding(actor_session_token=self.other_actor_session),
            self.binding(tenant_id="tenant-2"),
            self.binding(task_id="task-2"),
            self.binding(invocation_id="invocation-2"),
            self.binding(arguments={"report": "changed", "count": 3}),
            self.binding(staging_sha256=hashlib.sha256(b"other").hexdigest()),
        )
        for changed in changes:
            self.assertFalse(self.store.consume(
                approval, changed, current_approval_revision="revision-1"
            ))
        self.assertTrue(self.store.consume(
            approval, binding, current_approval_revision="revision-1"
        ))

    def test_consumption_rejects_revoked_or_wrong_actor_session(self):
        binding = self.binding()
        approval = self.issue(binding)
        self.assertFalse(self.consume(approval, binding, actor_session_token=self.other_actor_session))
        revoked = self.auth.authenticate("user.one", "correct-horse-battery-staple").token
        self.assertTrue(self.auth.revoke_session(revoked))
        self.assertFalse(self.consume(approval, binding, actor_session_token=revoked))
        self.assertTrue(self.consume(approval, binding))

    def test_revoked_actor_session_is_rejected(self):
        token = self.auth.authenticate(
            "user.one", "correct-horse-battery-staple"
        ).token
        self.assertTrue(self.auth.revoke_session(token))
        with self.assertRaises(InvocationApprovalError):
            self.binding(actor_session_token=token)

    def test_revoked_approver_session_is_rejected(self):
        binding = self.binding()
        token = self.auth.authenticate(
            "operator.one", "another-correct-horse-battery"
        ).token
        self.assertTrue(self.auth.revoke_session(token))
        with self.assertRaises(InvocationApprovalError):
            self.issue(binding, approver_session_token=token)

    def test_user_without_approval_permission_cannot_issue(self):
        with self.assertRaises(InvocationApprovalError):
            self.issue(approver_session_token=self.actor_session)

    def test_approval_revision_mismatch_fails_closed(self):
        binding = self.binding()
        approval = self.issue(binding)
        self.assertFalse(self.store.consume(
            approval, binding, current_approval_revision="revision-2"
        ))
        self.assertTrue(self.store.consume(
            approval, binding, current_approval_revision="revision-1"
        ))

    def test_manifest_and_grant_changes_invalidate_approval(self):
        binding = self.binding()
        approval = self.issue(binding)
        altered = dict(self.manifest.as_dict())
        altered["artifact_sha256"] = "f" * 64
        changed_manifest = build_invocation_binding(
            validate_manifest(altered), **self.kwargs
        )
        changed_grants = self.binding(write_grants=("output/other.json",))
        for changed in (changed_manifest, changed_grants):
            self.assertFalse(self.store.consume(
                approval, changed, current_approval_revision="revision-1"
            ))
        self.assertTrue(self.store.consume(
            approval, binding, current_approval_revision="revision-1"
        ))

    def test_unsafe_staging_paths_are_rejected(self):
        for path in (
            "../secret", "/etc/passwd", "C:/secret.txt", "dir//file.txt",
            "dir/", "dir/./file.txt", "dir/../file.txt", "dir\\\\file.txt",
            "dir\\x00file.txt",
        ):
            with self.subTest(path=path), self.assertRaises(InvocationApprovalError):
                self.binding(read_grants=(path,))

    def test_write_grant_requires_manifest_capability(self):
        restricted = dict(self.manifest.as_dict())
        restricted["capabilities"] = ["read_staged_input"]
        manifest = validate_manifest(restricted)
        with self.assertRaises(InvocationApprovalError):
            build_invocation_binding(manifest, **self.kwargs)

    def test_database_file_is_private_on_posix(self):
        if os.name == "nt":
            self.skipTest("POSIX file mode is not available on Windows")
        self.assertEqual(os.stat(self.store.database).st_mode & 0o777, 0o600)

    def test_concurrent_consumption_has_exactly_one_winner(self):
        binding = self.binding()
        approval = self.issue(binding)
        barrier = threading.Barrier(3)
        results = []
        lock = threading.Lock()

        def consume():
            barrier.wait()
            result = self.store.consume(
                approval, binding, current_approval_revision="revision-1"
            )
            with lock:
                results.append(result)

        threads = [threading.Thread(target=consume) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)

    def test_non_json_arguments_and_invalid_ttl_are_rejected(self):
        with self.assertRaises(InvocationApprovalError):
            self.binding(arguments={"bad": float("nan")})
        with self.assertRaises(InvocationApprovalError):
            self.issue(ttl_seconds=0)


if __name__ == "__main__":
    unittest.main()
