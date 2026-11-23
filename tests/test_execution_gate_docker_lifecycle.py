import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.execution_gate_docker import build_gate_container_create_argv
from core.execution_gate_docker_lifecycle import (
    CreatedGateContainer, DockerGateContainerClient, DockerGateContainerNotFound,
    ExecutionGateDockerLifecycleError, _bounded_subprocess_run,
)
from core.sandbox_policy import SandboxPolicy


CONTAINER_ID = "a" * 64
CONTAINER_NAME = "yuki-" + "b" * 32
IMAGE = "python@sha256:" + "c" * 64


class DockerGateContainerClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.docker = root / "docker"
        self.docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.docker.chmod(0o700)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.gate_dir = root / "gate"
        self.gate_dir.mkdir(mode=0o700)
        self.calls = []
        self.responses = []

        def runner(argv, **kwargs):
            self.calls.append((argv, kwargs))
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

        self.client = DockerGateContainerClient(
            docker_binary=str(self.docker), docker_host="unix:///run/user/1000/docker.sock",
            runner=runner,
        )
        self.labels = {
            "yuki.managed_by": "yuki", "yuki.task_id": "task-1",
            "yuki.worker_id": "worker-1", "yuki.owner_sha256": "d" * 64,
            "yuki.profile_id": "python-review",
            "yuki.image_digest": "sha256:" + "c" * 64,
        }
        self.argv = build_gate_container_create_argv(
            docker_binary=str(self.docker), policy=SandboxPolicy().validate(),
            image=IMAGE, workspace=str(self.workspace),
            gate_arguments=["--host-uid", "1000", "--task-id", "task-1",
                            "--worker-id", "worker-1", "--spec-id", "python-review",
                            "--image", IMAGE],
            private_gate_directory=str(self.gate_dir), container_name=CONTAINER_NAME,
            apparmor_profile="docker-default", container_labels=self.labels,
        )

    @staticmethod
    def result(code=0, stdout=b"", stderr=b""):
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)

    def test_create_returns_full_daemon_id_then_start_uses_that_id(self):
        self.responses.extend([
            self.result(stdout=(CONTAINER_ID + "\n").encode()),
            self.result(stdout=(CONTAINER_ID + "\n").encode()),
        ])
        container = self.client.create(argv=self.argv, container_name=CONTAINER_NAME)
        self.assertEqual(container, CreatedGateContainer(CONTAINER_NAME, CONTAINER_ID))
        self.client.start(container)
        self.assertEqual(self.calls[0][0], self.argv)
        self.assertEqual(self.calls[1][0], [str(self.docker.resolve()), "start", CONTAINER_ID])
        self.assertEqual(self.calls[0][1]["env"]["DOCKER_HOST"], "unix:///run/user/1000/docker.sock")

    def test_invalid_create_id_triggers_remove_and_fails_closed(self):
        self.responses.extend([
            self.result(stdout=b"short-id\n"),
            self.result(stdout=(CONTAINER_ID + "\n").encode()),
            self.result(),
        ])
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "full daemon-assigned"):
            self.client.create(argv=self.argv, container_name=CONTAINER_NAME)
        self.assertEqual(self.calls[1][0][-3:], ["--format", "{{.Id}}", CONTAINER_NAME])
        self.assertEqual(self.calls[2][0][-3:], ["rm", "-f", CONTAINER_ID])

    def test_create_failure_attempts_cleanup(self):
        self.responses.extend([
            self.result(code=1, stderr=b"daemon error"),
            self.result(code=1, stderr=b"Error: No such object: " + CONTAINER_NAME.encode()),
        ])
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "refused to create"):
            self.client.create(argv=self.argv, container_name=CONTAINER_NAME)
        self.assertEqual(self.calls[1][0][-3:], ["--format", "{{.Id}}", CONTAINER_NAME])

    def test_create_failure_surfaces_unverified_cleanup(self):
        self.responses.extend([
            self.result(code=1, stderr=b"daemon disconnected"),
            self.result(code=1, stderr=b"connection refused"),
        ])
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "create outcome is uncertain.*cleanup could not be verified"):
            self.client.create(argv=self.argv, container_name=CONTAINER_NAME)

    def test_create_interruption_reconciles_and_surfaces_cleanup_uncertainty(self):
        self.responses.extend([
            KeyboardInterrupt(),
            self.result(code=1, stderr=b"connection refused"),
        ])
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "create outcome is uncertain.*cleanup could not be verified"):
            self.client.create(argv=self.argv, container_name=CONTAINER_NAME)

    def test_verify_created_container_accepts_exact_daemon_identity(self):
        import json
        payload = [{
            "Id": CONTAINER_ID, "Name": "/" + CONTAINER_NAME,
            "Config": {"Image": IMAGE, "Labels": self.labels},
            "State": {"Running": False, "Status": "created"},
        }]
        self.responses.append(self.result(stdout=json.dumps(payload).encode()))
        container = CreatedGateContainer(CONTAINER_NAME, CONTAINER_ID)
        self.client.verify_created_container(
            container=container, expected_image=IMAGE, expected_labels=self.labels,
        )
        self.assertEqual(self.calls[0][0][-3:], ["--type", "container", CONTAINER_ID])

    def test_verify_created_container_removes_identity_or_state_mismatches(self):
        import json
        cases = (
            {"Name": "/yuki-" + "e" * 32},
            {"Config": {"Image": "python:latest", "Labels": self.labels}},
            {"Config": {"Image": IMAGE, "Labels": {**self.labels, "yuki.task_id": "other"}}},
            {"State": {"Running": True, "Status": "running"}},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.calls.clear()
                payload = [{
                    "Id": CONTAINER_ID, "Name": "/" + CONTAINER_NAME,
                    "Config": {"Image": IMAGE, "Labels": self.labels},
                    "State": {"Running": False, "Status": "created"},
                    **overrides,
                }]
                self.responses.extend([
                    self.result(stdout=json.dumps(payload).encode()), self.result(),
                ])
                with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "failed daemon identity verification"):
                    self.client.verify_created_container(
                        container=CreatedGateContainer(CONTAINER_NAME, CONTAINER_ID),
                        expected_image=IMAGE, expected_labels=self.labels,
                    )
                self.assertEqual(self.calls[-1][0][-3:], ["rm", "-f", CONTAINER_ID])

    def test_start_failure_surfaces_unverified_cleanup(self):
        self.responses.extend([
            self.result(stdout=CONTAINER_ID.encode()),
            self.result(code=1, stderr=b"daemon disconnected"),
            self.result(code=1, stderr=b"connection refused"),
        ])
        container = self.client.create(argv=self.argv, container_name=CONTAINER_NAME)
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "start outcome is uncertain.*cleanup could not be verified"):
            self.client.start(container)

    def test_start_interruption_reconciles_container(self):
        self.responses.extend([
            self.result(stdout=CONTAINER_ID.encode()),
            KeyboardInterrupt(),
            self.result(),
        ])
        container = self.client.create(argv=self.argv, container_name=CONTAINER_NAME)
        with self.assertRaises(KeyboardInterrupt):
            self.client.start(container)
        self.assertEqual(self.calls[2][0][-3:], ["rm", "-f", CONTAINER_ID])

    def test_start_failure_removes_container(self):
        self.responses.extend([
            self.result(stdout=CONTAINER_ID.encode()),
            self.result(code=1, stderr=b"start failed"),
            self.result(),
        ])
        container = self.client.create(argv=self.argv, container_name=CONTAINER_NAME)
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "failed to start"):
            self.client.start(container)
        self.assertEqual(self.calls[2][0][-3:], ["rm", "-f", CONTAINER_ID])


    def test_create_timeout_resolves_name_and_removes_by_daemon_id(self):
        self.responses.extend([
            subprocess.TimeoutExpired(cmd="docker create", timeout=10),
            self.result(stdout=(CONTAINER_ID + "\n").encode()),
            self.result(),
        ])
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "could not complete"):
            self.client.create(argv=self.argv, container_name=CONTAINER_NAME)
        self.assertEqual(self.calls[1][0][-3:], ["--format", "{{.Id}}", CONTAINER_NAME])
        self.assertEqual(self.calls[2][0][-3:], ["rm", "-f", CONTAINER_ID])

    def test_start_timeout_reconciles_container_by_unique_name(self):
        self.responses.extend([
            self.result(stdout=CONTAINER_ID.encode()),
            subprocess.TimeoutExpired(cmd="docker start", timeout=10),
            self.result(),
        ])
        container = self.client.create(argv=self.argv, container_name=CONTAINER_NAME)
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "could not complete"):
            self.client.start(container)
        self.assertEqual(
            self.calls[2][0],
            [str(self.docker.resolve()), "rm", "-f", CONTAINER_ID],
        )



    def test_remove_container_uses_full_daemon_id(self):
        self.responses.append(self.result())
        self.assertTrue(self.client.remove_container(container_id=CONTAINER_ID))
        self.assertEqual(self.calls[0][0][-3:], ["rm", "-f", CONTAINER_ID])

    def test_remove_container_reconciles_explicit_absence(self):
        self.responses.extend([self.result(code=1, stderr=b"transport lost"), self.result(code=1, stderr=b"Error: No such object: " + CONTAINER_ID.encode())])
        self.assertTrue(self.client.remove_container(container_id=CONTAINER_ID))

    def test_remove_container_retries_once_when_inspect_confirms_presence(self):
        import json
        payload = [{"Id": CONTAINER_ID, "Name": "/" + CONTAINER_NAME,
            "Config": {"Image": IMAGE}, "HostConfig": {"RestartPolicy": {"Name": "no"}},
            "RestartCount": 0, "State": {"Running": False, "Status": "exited", "ExitCode": 0, "OOMKilled": False}}]
        self.responses.extend([self.result(code=1, stderr=b"busy"), self.result(stdout=json.dumps(payload).encode()), self.result()])
        self.assertTrue(self.client.remove_container(container_id=CONTAINER_ID))
        self.assertEqual(len(self.calls), 3)

    def test_remove_container_fails_closed_on_daemon_disconnect(self):
        self.responses.extend([self.result(code=1, stderr=b"rm lost"), self.result(code=1, stderr=b"Cannot connect to the Docker daemon")])
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "state could not be reconciled"):
            self.client.remove_container(container_id=CONTAINER_ID)

    def test_remove_container_rejects_invalid_id_without_runner(self):
        with self.assertRaises(ValueError):
            self.client.remove_container(container_id="short")
        self.assertEqual(self.calls, [])

    def test_remove_only_accepts_success_or_explicit_absence(self):
        self.responses.extend([
            self.result(code=1, stderr=b"No such container: " + CONTAINER_NAME.encode()),
            self.result(code=1, stderr=b"permission denied"),
        ])
        self.assertTrue(self.client.remove(container_name=CONTAINER_NAME))
        self.assertFalse(self.client.remove(container_name=CONTAINER_NAME))

    def test_rejects_non_gate_create_argv_and_invalid_container_identity(self):
        with self.assertRaisesRegex(ValueError, "fixed-entrypoint"):
            self.client.create(argv=[str(self.docker), "create", IMAGE, "sh"], container_name=CONTAINER_NAME)
        with self.assertRaises(ValueError):
            self.client.start(CreatedGateContainer(CONTAINER_NAME, "short"))



    def test_inspect_container_returns_authoritative_running_state(self):
        import json
        payload = [{"Id": CONTAINER_ID, "Name": "/" + CONTAINER_NAME,
            "Config": {"Image": IMAGE}, "HostConfig": {"RestartPolicy": {"Name": "no"}},
            "RestartCount": 0, "State": {
            "Running": True, "Status": "running", "ExitCode": 0, "OOMKilled": False,
        }}]
        self.responses.append(self.result(stdout=json.dumps(payload).encode()))
        state = self.client.inspect_container(container_id=CONTAINER_ID)
        self.assertTrue(state["running"])
        self.assertEqual(state["status"], "running")
        self.assertEqual(self.calls[0][0][-3:], ["--type", "container", CONTAINER_ID])

    def test_inspect_container_returns_authoritative_exit_state(self):
        import json
        payload = [{"Id": CONTAINER_ID, "Name": "/" + CONTAINER_NAME,
            "Config": {"Image": IMAGE}, "HostConfig": {"RestartPolicy": {"Name": "no"}},
            "RestartCount": 0, "State": {
            "Running": False, "Status": "exited", "ExitCode": 17, "OOMKilled": False,
        }}]
        self.responses.append(self.result(stdout=json.dumps(payload).encode()))
        state = self.client.inspect_container(container_id=CONTAINER_ID)
        self.assertFalse(state["running"])
        self.assertEqual(state["exit_code"], 17)

    def test_inspect_container_fails_closed_on_daemon_disconnect(self):
        self.responses.append(self.result(code=1, stderr=b"Cannot connect to the Docker daemon"))
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "could not establish container state"):
            self.client.inspect_container(container_id=CONTAINER_ID)

    def test_inspect_container_rejects_malformed_or_mismatched_identity(self):
        import json
        self.responses.extend([
            self.result(stdout=b"not-json"),
            self.result(stdout=json.dumps([{"Id": "f" * 64, "Name": "/" + CONTAINER_NAME,
             "Config": {"Image": IMAGE}, "HostConfig": {"RestartPolicy": {"Name": "no"}},
             "RestartCount": 0, "State": {
                "Running": False, "Status": "exited", "ExitCode": 0, "OOMKilled": False,
            }}]).encode()),
        ])
        for _ in range(2):
            with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "malformed or mismatched"):
                self.client.inspect_container(container_id=CONTAINER_ID)

    def test_default_runner_bounds_stdout_and_stderr_capture(self):
        self.docker.write_text(
            "#!/bin/sh\nhead -c 70000 /dev/zero\nprintf x >&2\n",
            encoding="utf-8",
        )
        self.docker.chmod(0o700)
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "capture ceiling"):
            _bounded_subprocess_run(
                [str(self.docker)], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                close_fds=True, timeout=2.0, check=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C"},
            )


    def test_inspect_container_returns_restart_and_immutable_configuration(self):
        import json
        payload = [{
            "Id": CONTAINER_ID, "Name": "/" + CONTAINER_NAME,
            "Config": {"Image": IMAGE}, "HostConfig": {"RestartPolicy": {"Name": "no"}},
            "RestartCount": 0, "State": {
                "Running": False, "Status": "exited", "ExitCode": 0, "OOMKilled": False,
            },
        }]
        self.responses.append(self.result(stdout=json.dumps(payload).encode()))
        state = self.client.inspect_container(container_id=CONTAINER_ID)
        self.assertEqual(state["name"], "/" + CONTAINER_NAME)
        self.assertEqual(state["image"], IMAGE)
        self.assertEqual(state["restart_policy"], "no")
        self.assertEqual(state["restart_count"], 0)

    def test_inspect_container_rejects_missing_restart_or_config_evidence(self):
        import json
        payload = [{
            "Id": CONTAINER_ID, "Name": "/" + CONTAINER_NAME,
            "Config": {"Image": IMAGE}, "HostConfig": {"RestartPolicy": {"Name": "no"}},
            "State": {"Running": False, "Status": "exited", "ExitCode": 0, "OOMKilled": False},
        }]
        self.responses.append(self.result(stdout=json.dumps(payload).encode()))
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "malformed or mismatched"):
            self.client.inspect_container(container_id=CONTAINER_ID)

    def test_inspect_container_rejects_restart_policy_and_count_anomalies(self):
        import json
        cases = [
            {"RestartCount": 1, "HostConfig": {"RestartPolicy": {"Name": "always"}}},
            {"RestartCount": -1},
            {"HostConfig": {"RestartPolicy": {"Name": "invalid"}}},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                payload = [{
                    "Id": CONTAINER_ID, "Name": "/" + CONTAINER_NAME,
                    "Config": {"Image": IMAGE}, "HostConfig": {"RestartPolicy": {"Name": "no"}},
                    "RestartCount": 0, "State": {
                        "Running": False, "Status": "exited", "ExitCode": 0, "OOMKilled": False,
                    }, **overrides,
                }]
                self.responses.append(self.result(stdout=json.dumps(payload).encode()))
                with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "malformed or mismatched"):
                    self.client.inspect_container(container_id=CONTAINER_ID)

    def test_follow_logs_reports_daemon_transport_failure_without_claiming_container_exit(self):
        self.docker.write_text(
            "#!/bin/sh\nprintf 'Cannot connect to the Docker daemon' >&2\nexit 1\n",
            encoding="utf-8",
        )
        self.docker.chmod(0o700)
        with self.assertRaisesRegex(
            ExecutionGateDockerLifecycleError,
            "log transport failed; container state is unknown.*cannot connect",
        ):
            self.client.follow_logs(
                container_id=CONTAINER_ID, should_stop=lambda: None,
                timeout_seconds=2.0, output_limit_bytes=4096,
            )

    def test_follow_logs_terminates_real_process_group_on_cancellation(self):
        self.docker.write_text(
            "#!/bin/sh\nwhile :; do printf x; sleep 0.02; done\n",
            encoding="utf-8",
        )
        self.docker.chmod(0o700)
        result = self.client.follow_logs(
            container_id=CONTAINER_ID,
            should_stop=lambda: "cancelled",
            timeout_seconds=2.0,
            output_limit_bytes=4096,
        )
        stdout, stderr, truncated, reason = result
        self.assertTrue(stdout)
        self.assertEqual(stderr, b"")
        self.assertFalse(truncated)
        self.assertEqual(reason, "cancelled")

    def test_follow_logs_reaps_process_group_when_stop_callback_is_interrupted(self):
        pid_file = Path(self.temp.name) / "follower.pid"
        self.docker.write_text(
            "#!/bin/sh\necho $$ > " + str(pid_file) + "\n"
            "trap '' TERM\nwhile :; do printf x; sleep 0.02; done\n",
            encoding="utf-8",
        )
        self.docker.chmod(0o700)

        def interrupt():
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self.client.follow_logs(
                container_id=CONTAINER_ID, should_stop=interrupt,
                timeout_seconds=2.0, output_limit_bytes=4096,
            )
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_follow_logs_terminates_real_process_group_on_deadline(self):
        self.docker.write_text(
            "#!/bin/sh\nwhile :; do printf x; sleep 0.02; done\n",
            encoding="utf-8",
        )
        self.docker.chmod(0o700)
        result = self.client.follow_logs(
            container_id=CONTAINER_ID,
            should_stop=lambda: None,
            timeout_seconds=0.1,
            output_limit_bytes=4096,
        )
        self.assertEqual(result[3], "timeout")
        self.assertFalse(result[2])

    def test_follow_logs_terminates_real_process_group_on_output_overflow(self):
        self.docker.write_text(
            "#!/bin/sh\nwhile :; do printf 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'; done\n",
            encoding="utf-8",
        )
        self.docker.chmod(0o700)
        result = self.client.follow_logs(
            container_id=CONTAINER_ID,
            should_stop=lambda: None,
            timeout_seconds=2.0,
            output_limit_bytes=64,
        )
        self.assertTrue(result[2])
        self.assertEqual(result[3], "output_limit")
        self.assertLessEqual(len(result[0]) + len(result[1]), 64)



if __name__ == "__main__":
    unittest.main()
