import hashlib
import tempfile
import threading
import unittest
from pathlib import Path

from core.auth import AuthenticatedUser
from core.skill_execution_manifest import validate_manifest
from core.skill_invocation_approval import (
    InvocationApprovalError, OneShotInvocationApprovalStore,
    build_invocation_binding,
)


class SkillInvocationApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = OneShotInvocationApprovalStore(Path(self.temp.name) / "approvals.db")
        self.manifest = validate_manifest({
            "protocol_version": 1, "skill_id": "report_builder", "skill_version": "1.0.0",
            "artifact_sha256": "a" * 64, "entrypoint": "package.entry:run",
            "input_schema_sha256": "b" * 64, "output_schema_sha256": "c" * 64,
            "runtime_image": "registry.example/yuki-runtime@sha256:" + "d" * 64,
            "environment_lock_sha256": "e" * 64, "capabilities": ["read_staged_input", "write_staged_output"],
            "resource_limits": {"timeout_seconds": 30, "memory_mb": 256, "cpu_millis": 500,
                "max_processes": 16, "max_input_bytes": 65536, "max_output_bytes": 262144},
            "network": "none", "mount_policy": "invocation_staging_only",
        })
        self.actor = AuthenticatedUser("user-1", "user.one", "user", frozenset({"workflow:read"}))
        self.approver = AuthenticatedUser("operator-1", "operator.one", "operator", frozenset({"workflow:read", "workflow:approve"}))
        self.kwargs = dict(actor=self.actor, tenant_id="tenant-1", task_id="task-1",
            invocation_id="invocation-1", arguments={"report": "monthly", "count": 3},
            staging_sha256=hashlib.sha256(b"stage").hexdigest(),
            read_grants=("input/report.json",), write_grants=("output/report.json",))

    def binding(self, **overrides):
        args = dict(self.kwargs)
        args.update(overrides)
        return build_invocation_binding(self.manifest, **args)

    def issue(self, binding=None):
        return self.store.issue(binding or self.binding(), approved_by=self.approver,
            approval_revision="revision-1", ttl_seconds=300)

    def test_binding_requires_authenticated_principal(self):
        with self.assertRaises(InvocationApprovalError):
            build_invocation_binding(self.manifest, **dict(self.kwargs, actor="user-1"))

    def test_approval_issue_requires_authenticated_approver_permission(self):
        with self.assertRaises(InvocationApprovalError):
            self.store.issue(self.binding(), approved_by=self.actor,
                approval_revision="revision-1", ttl_seconds=300)
        with self.assertRaises(InvocationApprovalError):
            self.store.issue(self.binding(), approved_by="operator-1",
                approval_revision="revision-1", ttl_seconds=300)

    def test_exact_binding_is_consumed_once(self):
        binding = self.binding()
        approval = self.issue(binding)
        self.assertTrue(self.store.consume(approval, binding, current_approval_revision="revision-1"))
        self.assertFalse(self.store.consume(approval, binding, current_approval_revision="revision-1"))

    def test_actor_tenant_task_invocation_arguments_and_staging_are_bound(self):
        binding = self.binding()
        approval = self.issue(binding)
        for changed in (
            self.binding(actor=AuthenticatedUser("user-2", "user.two", "user", frozenset({"workflow:read"}))),
            self.binding(tenant_id="tenant-2"),
            self.binding(task_id="task-2"),
            self.binding(invocation_id="invocation-2"),
            self.binding(arguments={"report": "changed", "count": 3}),
            self.binding(staging_sha256=hashlib.sha256(b"other").hexdigest()),
        ):
            self.assertFalse(self.store.consume(approval, changed, current_approval_revision="revision-1"))
        self.assertTrue(self.store.consume(approval, binding, current_approval_revision="revision-1"))

    def test_approval_revision_mismatch_fails_closed(self):
        binding = self.binding()
        approval = self.issue(binding)
        self.assertFalse(self.store.consume(approval, binding, current_approval_revision="revision-2"))
        self.assertTrue(self.store.consume(approval, binding, current_approval_revision="revision-1"))

    def test_wrong_manifest_artifact_image_or_grants_change_binding(self):
        binding = self.binding()
        approval = self.issue(binding)
        altered_manifest = dict(self.manifest.as_dict())
        altered_manifest["artifact_sha256"] = "f" * 64
        changed = build_invocation_binding(validate_manifest(altered_manifest), **self.kwargs)
        self.assertFalse(self.store.consume(approval, changed, current_approval_revision="revision-1"))
        changed = self.binding(write_grants=("output/other.json",))
        self.assertFalse(self.store.consume(approval, changed, current_approval_revision="revision-1"))
        self.assertTrue(self.store.consume(approval, binding, current_approval_revision="revision-1"))

    def test_unsafe_grants_and_capability_escalation_are_rejected(self):
        with self.assertRaises(InvocationApprovalError):
            self.binding(write_grants=("../secret",))
        with self.assertRaises(InvocationApprovalError):
            self.binding(read_grants=("/etc/passwd",))

    def test_rejects_windows_and_ambiguous_staging_paths(self):
        for path in ("C:/secret.txt", "dir//file.txt", "dir/", "dir/./file.txt",
                     "dir/../file.txt", "dir\\file.txt", "dir\x00file.txt"):
            with self.subTest(path=path), self.assertRaises(InvocationApprovalError):
                self.binding(read_grants=(path,))

    def test_database_file_is_private_on_posix(self):
        import os
        if os.name == "nt":
            self.skipTest("POSIX file mode is not available on Windows")
        self.assertEqual(os.stat(self.store.database).st_mode & 0o777, 0o600)

    def test_write_grant_requires_manifest_capability(self):
        restricted = dict(self.manifest.as_dict())
        restricted["capabilities"] = ["read_staged_input"]
        manifest = validate_manifest(restricted)
        with self.assertRaises(InvocationApprovalError):
            build_invocation_binding(manifest, **self.kwargs)

    def test_concurrent_consumption_has_exactly_one_winner(self):
        binding = self.binding()
        approval = self.issue(binding)
        barrier = threading.Barrier(3)
        results = []
        lock = threading.Lock()
        def consume():
            barrier.wait()
            result = self.store.consume(approval, binding, current_approval_revision="revision-1")
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

    def test_invalid_arguments_and_expiry_are_rejected(self):
        with self.assertRaises(InvocationApprovalError):
            self.binding(arguments={"bad": float("nan")})
        with self.assertRaises(InvocationApprovalError):
            self.store.issue(self.binding(), approved_by=self.approver,
                approval_revision="revision-1", ttl_seconds=0)


if __name__ == "__main__":
    unittest.main()
