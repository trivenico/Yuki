import pytest

from core.gate_first_host_preflight import (
    GateFirstHostPreflightError,
    _proc_mount_options,
    _require_verifier,
    validate_gate_first_host_environment,
)


def test_proc_mount_options_extracts_proc_mount_and_superblock_options():
    mountinfo = (
        "25 1 0:25 / / rw,relatime - ext4 /dev/root rw\n"
        "36 25 0:32 / /proc rw,nosuid,nodev,noexec,relatime - proc proc rw"
    )
    assert _proc_mount_options(mountinfo) == (
        "rw", "nosuid", "nodev", "noexec", "relatime", "rw"
    )


def test_proc_mount_options_rejects_missing_or_non_proc_mount():
    with pytest.raises(GateFirstHostPreflightError):
        _proc_mount_options("25 1 0:25 / / rw - ext4 /dev/root rw")


def test_verifier_must_return_literal_true():
    with pytest.raises(GateFirstHostPreflightError, match="positively verify"):
        _require_verifier("mapping verifier", lambda: 1)


def test_verifier_exceptions_are_redacted_and_fail_closed():
    def fail():
        raise OSError("sensitive deployment detail")

    with pytest.raises(GateFirstHostPreflightError, match="mapping verifier failed"):
        _require_verifier("mapping verifier", fail)


def test_non_linux_host_is_rejected_before_host_inspection():
    with pytest.raises(GateFirstHostPreflightError, match="requires Linux"):
        validate_gate_first_host_environment(
            docker_binary="/usr/bin/docker",
            expected_docker_sha256="0" * 64,
            docker_socket_path="/var/run/docker.sock",
            private_directory_root="/tmp",
            expected_host_uid=1000,
            uid_resolver=lambda _pid: 1000,
            exec_observer=lambda *_args, **_kwargs: True,
            supervisor_collector_factory=lambda **_kwargs: object(),
            trusted_evidence_builder=lambda *_args: object(),
            user_namespace_mapping_verifier=lambda: True,
            release_publisher_ownership_verifier=lambda: True,
            platform="darwin",
        )
