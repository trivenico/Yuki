import tempfile
import unittest
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from core.skill_container_request import SkillContainerRequest
from core.skill_container_launcher import (
    SkillContainerLaunchError,
    build_skill_docker_argv,
    launch_skill_container,
)


class SkillContainerLauncherArgvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.input_schema_bytes = b'{"type":"object"}'
        self.output_schema_bytes = b'{"type":"object"}'
        self.request = SkillContainerRequest(
            actor_id="actor", tenant_id="tenant", task_id="task",
            invocation_id="invocation", skill_id="skill", worker_id="worker",
            fencing_token=1, binding_sha256="a"*64, manifest_sha256="b"*64,
            artifact_sha256="c"*64, staging_sha256="d"*64, argument_sha256="e"*64,
            entrypoint="pkg.entry:run",
            input_schema_sha256=hashlib.sha256(self.input_schema_bytes).hexdigest(),
            output_schema_sha256=hashlib.sha256(self.output_schema_bytes).hexdigest(), capabilities=("read_staged_input",),
            read_grants=(), write_grants=(),
            image="registry.example/yuki-runtime@sha256:"+"1"*64,
            workspace=str(Path(self.tmp.name).resolve()), mount_target="/yuki/skill",
            mount_read_only=True, command=("/opt/yuki/bin/skill-runner", "--protocol-v1"),
            network_mode="none", read_only_root=True, non_root=True,
            drop_all_capabilities=True, no_new_privileges=True,
            timeout_seconds=30, memory_mb=256, cpu_millis=500,
            max_processes=16, max_input_bytes=65536, max_output_bytes=262144,
        )

    def test_argv_enforces_digest_image_and_isolation_flags(self):
        argv = build_skill_docker_argv(self.request, container_name="yuki-skill-"+"a"*24)
        self.assertIn("--pull=never", argv)
        self.assertIn("--network", argv)
        self.assertEqual(argv[argv.index("--network")+1], "none")
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop", argv)
        self.assertIn("--init", argv)
        self.assertIn("--ipc", argv)
        self.assertEqual(argv[argv.index("--user")+1], "65532:65532")
        mount = argv[argv.index("--mount")+1]
        self.assertTrue(mount.endswith(",readonly"))

    def test_rejects_mutable_tag_and_unsafe_request(self):
        from dataclasses import replace
        with self.assertRaises(SkillContainerLaunchError):
            build_skill_docker_argv(replace(self.request, image="python:3.12"), container_name="yuki-skill-"+"a"*24)
        with self.assertRaises(SkillContainerLaunchError):
            build_skill_docker_argv(replace(self.request, network_mode="bridge"), container_name="yuki-skill-"+"a"*24)
        with self.assertRaises(SkillContainerLaunchError):
            build_skill_docker_argv(self.request, container_name="arbitrary-name")

    @patch("core.skill_container_launcher.subprocess.Popen")
    def test_live_authorization_denial_prevents_docker_launch(self, popen):
        from dataclasses import replace
        arguments = {"input": "safe"}
        digest = hashlib.sha256(json.dumps(
            arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")).hexdigest()
        request = replace(self.request, argument_sha256=digest)
        with self.assertRaises(SkillContainerLaunchError):
            launch_skill_container(request, arguments, authorize=lambda _request: False,
                input_schema_bytes=self.input_schema_bytes, output_schema_bytes=self.output_schema_bytes)
        popen.assert_not_called()

    @patch("core.skill_container_launcher.subprocess.Popen")
    def test_authorization_exception_fails_closed(self, popen):
        from dataclasses import replace
        arguments = {"input": "safe"}
        digest = hashlib.sha256(json.dumps(
            arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")).hexdigest()
        request = replace(self.request, argument_sha256=digest)
        def broken_authorizer(_request):
            raise RuntimeError("store unavailable")
        with self.assertRaises(SkillContainerLaunchError):
            launch_skill_container(request, arguments, authorize=broken_authorizer,
                input_schema_bytes=self.input_schema_bytes, output_schema_bytes=self.output_schema_bytes)
        popen.assert_not_called()

    def test_rejects_write_capability_until_output_mount_is_supported(self):
        from dataclasses import replace
        with self.assertRaises(SkillContainerLaunchError):
            build_skill_docker_argv(
                replace(self.request, capabilities=("read_staged_input", "write_staged_output")),
                container_name="yuki-skill-"+"a"*24,
            )
        with self.assertRaises(SkillContainerLaunchError):
            build_skill_docker_argv(
                replace(self.request, write_grants=("reports/result.json",)),
                container_name="yuki-skill-"+"a"*24,
            )

    def test_rejects_mount_option_injection_path(self):
        from dataclasses import replace
        with self.assertRaises(SkillContainerLaunchError):
            build_skill_docker_argv(
                replace(self.request, workspace=self.tmp.name+",readonly=false"),
                container_name="yuki-skill-"+"a"*24,
            )

    def test_rejects_noncanonical_workspace(self):
        from dataclasses import replace
        with self.assertRaises(SkillContainerLaunchError):
            build_skill_docker_argv(replace(self.request, workspace=self.tmp.name+"/.."), container_name="yuki-skill-"+"a"*24)


if __name__ == "__main__":
    unittest.main()
