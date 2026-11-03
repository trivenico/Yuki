import hashlib
import tempfile
import unittest
from pathlib import Path

from core.skill_execution_manifest import validate_manifest
from core.skill_invocation_approval import build_invocation_binding
from core.skill_staging import stage_skill_artifact
from core.skill_container_request import (
    SkillContainerRequestError,
    build_skill_container_request,
    build_skill_runtime_input,
)


class SkillContainerRequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.artifact = b"not actually executed"
        # The staging API expects a ZIP archive; build the smallest valid package.
        import io
        import zipfile
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("package/entry.py", b"def run(): return 1")
        self.artifact = archive.getvalue()
        self.manifest = validate_manifest({
            "protocol_version": 1, "skill_id": "report_builder", "skill_version": "1.0.0",
            "artifact_sha256": hashlib.sha256(self.artifact).hexdigest(),
            "entrypoint": "package.entry:run", "input_schema_sha256": "b"*64,
            "output_schema_sha256": "c"*64,
            "runtime_image": "registry.example/yuki-runtime@sha256:"+"d"*64,
            "environment_lock_sha256": "e"*64, "capabilities": ["read_staged_input"],
            "resource_limits": {"timeout_seconds":30,"memory_mb":256,"cpu_millis":500,
                "max_processes":16,"max_input_bytes":65536,"max_output_bytes":262144},
            "network":"none","mount_policy":"invocation_staging_only",
        })

        from core.auth import BuiltInAuthService
        self.auth = BuiltInAuthService(str(Path(self.tmp.name)/"auth.db"), password_iterations=100_000)
        self.auth.bootstrap_admin("operator", "another-correct-horse-battery")
        self.auth.create_account("actor", "correct-horse-battery-staple")
        self.session = self.auth.authenticate("actor", "correct-horse-battery-staple").token

    def _binding(self, staged, arguments=None):
        if arguments is None:
            arguments = {"format": "json"}
        return build_invocation_binding(
            self.manifest, auth_service=self.auth, actor_session_token=self.session,
            tenant_id="tenant", task_id="task", invocation_id="invocation",
            arguments=arguments, staging_sha256=staged.tree_sha256,
        )

    def test_builds_fixed_restrictive_request_from_matching_binding(self):
        with stage_skill_artifact(self.artifact, self.manifest, staging_parent=self.tmp.name) as staged:
            binding = self._binding(staged)
            request = build_skill_container_request(
                self.manifest, staged, binding, worker_id="worker-1", fencing_token=1,
            )
            self.assertEqual(request.image, self.manifest.runtime_image)
            self.assertEqual(request.command, ("/opt/yuki/bin/skill-runner", "--protocol-v1"))
            self.assertEqual(request.network_mode, "none")
            self.assertTrue(request.mount_read_only and request.read_only_root)
            self.assertTrue(request.non_root and request.drop_all_capabilities)
            self.assertTrue(request.no_new_privileges)
            self.assertEqual(len(request.canonical_sha256()), 64)

    def test_rejects_changed_fencing_token(self):
        with stage_skill_artifact(self.artifact, self.manifest, staging_parent=self.tmp.name) as staged:
            binding = self._binding(staged)
            for token in (0, -1, True, "1"):
                with self.subTest(token=token), self.assertRaises(SkillContainerRequestError):
                    build_skill_container_request(
                        self.manifest, staged, binding, worker_id="worker-1", fencing_token=token,
                    )

    def test_rejects_changed_staged_content_after_approval(self):
        with stage_skill_artifact(self.artifact, self.manifest, staging_parent=self.tmp.name) as staged:
            binding = self._binding(staged)
            file = staged.root / "package/entry.py"
            file.chmod(0o600)
            file.write_text("def run(): return 2")
            with self.assertRaises(SkillContainerRequestError):
                build_skill_container_request(
                    self.manifest, staged, binding, worker_id="worker-1", fencing_token=1,
                )

    def test_runtime_input_matches_approved_digest_and_size_limit(self):
        arguments = {"format": "json"}
        with stage_skill_artifact(self.artifact, self.manifest, staging_parent=self.tmp.name) as staged:
            binding = self._binding(staged, arguments)
            payload = build_skill_runtime_input(binding, arguments, max_input_bytes=65536)
            self.assertEqual(payload, b'{"format":"json"}')
            with self.assertRaises(SkillContainerRequestError):
                build_skill_runtime_input(binding, {"format": "csv"}, max_input_bytes=65536)
            with self.assertRaises(SkillContainerRequestError):
                build_skill_runtime_input(binding, arguments, max_input_bytes=2)

    def test_rejects_binding_for_different_manifest(self):
        with stage_skill_artifact(self.artifact, self.manifest, staging_parent=self.tmp.name) as staged:
            binding = self._binding(staged)
            changed = validate_manifest(dict(self.manifest.as_dict(), skill_version="1.0.1"))
            with self.assertRaises(SkillContainerRequestError):
                build_skill_container_request(
                    changed, staged, binding, worker_id="worker-1", fencing_token=1,
                )


if __name__ == "__main__":
    unittest.main()
