import tempfile
import unittest
from pathlib import Path

from automation.sandbox_execution_submission import SandboxExecutionSubmissionService
from automation.sandbox_execution_runtime import (
    SandboxExecutionRuntime, build_sandbox_execution_runtime,
)
from core.auth import BuiltInAuthService


class SandboxExecutionRuntimeCompositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(
            str(self.root / "auth.sqlite3"), password_iterations=100_000,
        )
        self.auth.bootstrap_admin("runtime.admin", "Secure runtime password 8!")

    def test_runtime_is_disabled_by_default(self):
        self.assertIsNone(build_sandbox_execution_runtime(
            {"workspace": str(self.root)}, auth_service=self.auth,
        ))

    def test_non_boolean_opt_in_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            build_sandbox_execution_runtime(
                {"workspace": str(self.root), "sandbox_execution_enabled": "true"},
                auth_service=self.auth,
            )

    def test_enabled_runtime_requires_digest_pinned_image_allowlist(self):
        with self.assertRaisesRegex(ValueError, "digest-pinned"):
            build_sandbox_execution_runtime(
                {"workspace": str(self.root), "sandbox_execution_enabled": True,
                 "sandbox_execution_allowed_images": []},
                auth_service=self.auth,
            )

    def test_enabled_runtime_composes_separate_private_stores_and_closes(self):
        image = "registry.example/yuki@sha256:" + "a" * 64
        runtime = build_sandbox_execution_runtime(
            {"workspace": str(self.root), "sandbox_execution_enabled": True,
             "sandbox_execution_allowed_images": [image]},
            auth_service=self.auth,
        )
        self.assertIsInstance(runtime, SandboxExecutionRuntime)
        self.assertIsInstance(runtime.submission_service, SandboxExecutionSubmissionService)
        self.assertTrue(runtime.sandbox.enabled)
        self.assertEqual(runtime.sandbox.allowed_images, frozenset({image}))
        self.assertNotEqual(runtime.engine.db_path, self.auth.database_path)
        self.assertNotEqual(runtime.audit_log.database_path, self.auth.database_path)
        self.assertNotEqual(runtime.engine.db_path, runtime.audit_log.database_path)
        self.assertTrue(runtime.sandbox.workspace.is_dir())
        self.assertTrue(runtime.audit_log.verify_chain())
        runtime.close()
        runtime.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            runtime.engine.create_goal()

    def test_image_allowlist_requires_a_complete_sha256_digest(self):
        with self.assertRaisesRegex(ValueError, "image allow-list"):
            build_sandbox_execution_runtime(
                {"workspace": str(self.root), "sandbox_execution_enabled": True,
                 "sandbox_execution_allowed_images": ["registry.example/yuki@sha256:abc"]},
                auth_service=self.auth,
            )

    def test_worker_id_must_be_bounded_non_empty_text(self):
        image = "registry.example/yuki@sha256:" + "c" * 64
        with self.assertRaisesRegex(ValueError, "worker ID"):
            build_sandbox_execution_runtime(
                {"workspace": str(self.root), "sandbox_execution_enabled": True,
                 "sandbox_execution_allowed_images": [image],
                 "sandbox_execution_worker_id": " "},
                auth_service=self.auth,
            )

    def test_workspace_cannot_overlap_private_execution_state(self):
        image = "registry.example/yuki@sha256:" + "b" * 64
        with self.assertRaisesRegex(ValueError, "isolated"):
            build_sandbox_execution_runtime(
                {"workspace": str(self.root), "sandbox_execution_enabled": True,
                 "sandbox_execution_allowed_images": [image],
                 "sandbox_execution_workspace": str(self.root / "sandbox-execution-state" / "nested")},
                auth_service=self.auth,
            )
