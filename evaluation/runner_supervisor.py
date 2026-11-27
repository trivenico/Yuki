"""Fail-closed ordering contract for a future isolated evaluation supervisor.

This is orchestration logic over an injected adapter, not a Linux sandbox. No
production adapter is provided here. A deployment must implement every adapter
operation with pinned descriptors, authenticated runner identity, kernel
isolation, bounded waits, and independently verified cleanup before enabling it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import os
import re
import stat
from typing import Any, Protocol

from evaluation.runner_isolation_policy import RunnerIsolationPolicy


class RunnerLaunchState(str, Enum):
    NEW = "new"
    CGROUP_CREATED = "cgroup_created"
    POLICY_APPLIED = "policy_applied"
    POLICY_VERIFIED = "policy_verified"
    CHILD_FORKED_BLOCKED = "child_forked_blocked"
    CHILD_PLACED = "child_placed"
    MEMBERSHIP_VERIFIED = "membership_verified"
    EXEC_RELEASED = "exec_released"
    TERMINAL = "terminal"
    CLEANED = "cleaned"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RunnerWaitResult:
    """Bounded wait outcome; timeout is distinct from a child exit status."""

    exit_code: int | None
    timed_out: bool


@dataclass(frozen=True, slots=True)
class RunnerLaunchResult:
    state: RunnerLaunchState
    exit_code: int | None
    cgroup_handle: Any
    child_handle: Any
    events: tuple[str, ...]
    failure: str
    artifact_sha256: str
    policy_digest: str


class RunnerSupervisorAdapter(Protocol):
    """Narrow OS boundary. Implementations must not accept arbitrary shell text."""

    def create_cgroup(self, policy: RunnerIsolationPolicy) -> Any:
        """Return an owned handle, or fail atomically without leaving a cgroup."""
        ...
    def apply_policy(self, cgroup: Any, settings: dict[str, str]) -> None: ...
    def verify_policy(self, cgroup: Any, policy: RunnerIsolationPolicy) -> bool: ...
    def fork_blocked_child(self, cgroup: Any, artifact_descriptor: int) -> Any:
        """Return a child handle only after a trusted bootstrap reaches its gate.

        The bootstrap must not execute candidate bytes. It must close every
        descriptor except an explicit allowlist (stdio, the sealed artifact FD,
        and the one-shot gate channel), then report readiness while the gate
        remains closed. If creation partially succeeds but no handle is
        returned, the adapter must abort and reap the child before raising.
        """
        ...
    def verify_child_identity(self, child: Any) -> bool:
        """Bind the handle to one live process without PID-reuse ambiguity.

        A Linux implementation must retain a pidfd and independently bind it
        to the child's /proc start-time identity; a numeric PID alone is not
        sufficient. The check must fail closed if pidfd support or identity
        verification is unavailable.
        """
        ...
    def verify_descriptor_allowlist(self, child: Any, artifact_descriptor: int) -> bool:
        """Verify the child's open-FD set against the documented allowlist.

        The adapter must inspect the actual child, not merely echo its intended
        spawn configuration. No ambient host, credential, socket, or control
        descriptors may survive into the candidate process.
        """
        ...
    def verify_exec_barrier(self, child: Any) -> bool:
        """Prove the trusted bootstrap is waiting before candidate execution.

        This must be a completed child-to-parent readiness handshake; process
        existence or successful spawn is not proof that the gate is closed.
        The release channel must be one-shot and unavailable to the candidate
        before the parent authorizes release.
        """
        ...
    def place_child(self, child: Any, cgroup: Any) -> None: ...
    def verify_membership(self, child: Any, cgroup: Any) -> bool: ...
    def verify_artifact(self, child: Any, artifact_descriptor: int, expected_sha256: str) -> bool:
        """Prove the child is bound to immutable bytes from this FD.

        Implementations must prevent writes/replacement through exec (for
        example, require a fully sealed memfd) and must never reopen a path.
        """
        ...
    def release_exec(self, child: Any) -> None: ...
    def wait_for_child(self, child: Any, timeout_seconds: int) -> RunnerWaitResult:
        """Use a monotonic bounded wait; timeout does not imply child termination.

        On timeout return RunnerWaitResult(None, True) promptly. The coordinator
        will then request abort/reap and independently verify the child state.
        """
        ...
    def abort_and_reap_child(self, child: Any) -> None:
        """Bounded, idempotent termination/reap request; never silently detach."""
        ...
    def verify_child_reaped(self, child: Any) -> bool: ...
    def verify_cgroup_empty(self, cgroup: Any) -> bool: ...
    def remove_cgroup(self, cgroup: Any) -> None: ...



def _pin_artifact_descriptor(descriptor: int) -> int:
    """Duplicate and validate an artifact FD before handing it to the adapter.

    The duplicate is non-inheritable and remains owned by this coordinator
    through child cleanup. Only regular files (including memfd-backed files)
    are accepted. The adapter must still verify artifact identity/digest and
    consume this descriptor without reopening a caller-controlled path.
    """
    pinned = -1
    try:
        pinned = os.dup(descriptor)
        os.set_inheritable(pinned, False)
        if not stat.S_ISREG(os.fstat(pinned).st_mode):
            raise ValueError("artifact descriptor must refer to a regular file")
        return pinned
    except BaseException:
        if pinned >= 0:
            try:
                os.close(pinned)
            except OSError:
                pass
        raise



def _verify_immutable_memfd(descriptor: int) -> None:
    """Require kernel-enforced write, size, and seal immutability before launch.

    A regular-file check and digest alone are insufficient: another holder of
    a writable descriptor could change bytes after the final hash. F_GET_SEALS
    is deliberately required; unsupported platforms and non-memfd descriptors
    fail closed.
    """
    try:
        import fcntl
    except ImportError as exc:
        raise RuntimeError("immutable Linux memfd sealing is unavailable") from exc
    required_names = ("F_SEAL_SEAL", "F_SEAL_SHRINK", "F_SEAL_GROW", "F_SEAL_WRITE")
    if not hasattr(fcntl, "F_GET_SEALS") or any(
        not hasattr(fcntl, name) for name in required_names
    ):
        raise RuntimeError("immutable Linux memfd sealing is unavailable")
    required = 0
    for name in required_names:
        required |= getattr(fcntl, name)
    try:
        actual = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
    except OSError as exc:
        raise ValueError("artifact descriptor must be a sealed memfd") from exc
    if actual & required != required:
        raise ValueError("artifact memfd is missing required immutable seals")



def _sha256_descriptor(descriptor: int) -> str:
    """Hash a regular file without changing its shared file offset."""
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("artifact descriptor must refer to a regular file")
    digest = hashlib.sha256()
    offset = 0
    while offset < before.st_size:
        chunk = os.pread(descriptor, min(1024 * 1024, before.st_size - offset), offset)
        if not chunk:
            raise ValueError("artifact changed or truncated while hashing")
        digest.update(chunk)
        offset += len(chunk)
    after = os.fstat(descriptor)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    if identity_before != identity_after:
        raise ValueError("artifact changed while hashing")
    return digest.hexdigest()


def run_supervised_attempt(
    *,
    adapter: RunnerSupervisorAdapter,
    policy: RunnerIsolationPolicy,
    artifact_descriptor: int,
    expected_artifact_sha256: str,
) -> RunnerLaunchResult:
    """Run the required pre-exec sequence; any failed gate prevents exec release.

    The coordinator pins and hashes the staged artifact descriptor for the full attempt.
    The adapter owns child synchronization, controlled descriptor inheritance,
    process identity, timeout enforcement, output capture, and kernel operations.
    This function accepts only a pre-staged descriptor and a validated policy; it does not
    accept command strings, environment variables, or network settings.
    """
    if not isinstance(policy, RunnerIsolationPolicy) or not policy.verify():
        raise ValueError("runner isolation policy is invalid")
    if type(artifact_descriptor) is not int or artifact_descriptor < 0:
        raise ValueError("artifact descriptor must be a non-negative integer")

    if type(expected_artifact_sha256) is not str or not re.fullmatch(r"[a-f0-9]{64}", expected_artifact_sha256):
        raise ValueError("expected artifact SHA-256 must be 64 lowercase hexadecimal characters")
    pinned_artifact_descriptor = _pin_artifact_descriptor(artifact_descriptor)
    try:
        _verify_immutable_memfd(pinned_artifact_descriptor)
        observed_artifact_sha256 = _sha256_descriptor(pinned_artifact_descriptor)
        if observed_artifact_sha256 != expected_artifact_sha256:
            raise ValueError("artifact digest does not match authorization")
    except BaseException:
        os.close(pinned_artifact_descriptor)
        raise

    state = RunnerLaunchState.NEW
    events = [state.value]
    cgroup = None
    child = None
    child_reaped = True
    exit_code = None
    failure = ""
    cleanup_interruption: BaseException | None = None

    def advance(next_state: RunnerLaunchState) -> None:
        nonlocal state
        state = next_state
        events.append(state.value)

    try:
        cgroup = adapter.create_cgroup(policy)
        if cgroup is None:
            raise RuntimeError("cgroup_creation_failed")
        advance(RunnerLaunchState.CGROUP_CREATED)

        adapter.apply_policy(cgroup, policy.cgroup_v2_settings())
        advance(RunnerLaunchState.POLICY_APPLIED)

        if adapter.verify_policy(cgroup, policy) is not True:
            raise RuntimeError("policy_readback_failed")
        advance(RunnerLaunchState.POLICY_VERIFIED)

        child = adapter.fork_blocked_child(cgroup, pinned_artifact_descriptor)
        if child is None:
            raise RuntimeError("blocked_child_creation_failed")
        advance(RunnerLaunchState.CHILD_FORKED_BLOCKED)

        if adapter.verify_child_identity(child) is not True:
            raise RuntimeError("child_identity_verification_failed")
        if adapter.verify_descriptor_allowlist(child, pinned_artifact_descriptor) is not True:
            raise RuntimeError("child_descriptor_allowlist_verification_failed")
        if adapter.verify_exec_barrier(child) is not True:
            raise RuntimeError("child_exec_barrier_verification_failed")

        adapter.place_child(child, cgroup)
        advance(RunnerLaunchState.CHILD_PLACED)

        if adapter.verify_membership(child, cgroup) is not True:
            raise RuntimeError("child_membership_verification_failed")
        advance(RunnerLaunchState.MEMBERSHIP_VERIFIED)

        if adapter.verify_artifact(child, pinned_artifact_descriptor, expected_artifact_sha256) is not True:
            raise RuntimeError("child_artifact_binding_verification_failed")
        # Detect mutation during child staging. The adapter must additionally
        # guarantee immutable bytes (for example, a sealed memfd) through exec.
        if _sha256_descriptor(pinned_artifact_descriptor) != expected_artifact_sha256:
            raise RuntimeError("artifact_changed_before_exec")

        adapter.release_exec(child)
        advance(RunnerLaunchState.EXEC_RELEASED)

        wait_result = adapter.wait_for_child(child, policy.wall_time_seconds)
        if not isinstance(wait_result, RunnerWaitResult):
            raise RuntimeError("invalid_child_wait_result")
        if type(wait_result.timed_out) is not bool:
            raise RuntimeError("invalid_child_timeout_flag")
        if wait_result.timed_out:
            if wait_result.exit_code is not None:
                raise RuntimeError("timeout_result_must_not_claim_exit_status")
            raise TimeoutError("child_wall_time_exceeded")
        if type(wait_result.exit_code) is not int or not 0 <= wait_result.exit_code <= 255:
            raise RuntimeError("invalid_child_exit_status")
        exit_code = wait_result.exit_code
        if adapter.verify_child_reaped(child) is not True:
            raise RuntimeError("child_reap_not_verified")
        child_reaped = True
        advance(RunnerLaunchState.TERMINAL)
    except BaseException as exc:
        # Cancellation and process-control exceptions must not bypass child
        # termination/reap or cgroup cleanup. Preserve their semantics by
        # re-raising non-Exception BaseExceptions after the cleanup finally.
        failure = type(exc).__name__ + ":" + state.value
        if child is not None:
            child_reaped = False
            try:
                adapter.abort_and_reap_child(child)
                child_reaped = adapter.verify_child_reaped(child) is True
                if not child_reaped:
                    failure += ";child_reap_unverified"
            except BaseException as cleanup_exc:
                if isinstance(cleanup_exc, Exception):
                    failure += ";child_cleanup_failed"
                else:
                    cleanup_interruption = cleanup_exc
                    failure += ";child_cleanup_interrupted"
        advance(RunnerLaunchState.FAILED)
        if not isinstance(exc, Exception):
            raise
    finally:
        if cgroup is not None:
            try:
                if not child_reaped:
                    raise RuntimeError("child_reap_unverified")
                if adapter.verify_cgroup_empty(cgroup) is not True:
                    raise RuntimeError("cgroup_not_empty")
                adapter.remove_cgroup(cgroup)
            except BaseException as cleanup_exc:
                if not isinstance(cleanup_exc, Exception):
                    cleanup_interruption = cleanup_interruption or cleanup_exc
                failure = (failure + ";" if failure else "") + "cgroup_cleanup_unverified"
                if state is not RunnerLaunchState.FAILED:
                    advance(RunnerLaunchState.FAILED)
        try:
            os.close(pinned_artifact_descriptor)
        except BaseException as cleanup_exc:
            failure = (failure + ";" if failure else "") + "artifact_descriptor_close_failed"
            if not isinstance(cleanup_exc, Exception):
                cleanup_interruption = cleanup_interruption or cleanup_exc
            if state is not RunnerLaunchState.FAILED:
                advance(RunnerLaunchState.FAILED)

    if state is not RunnerLaunchState.FAILED:
        advance(RunnerLaunchState.CLEANED)
    if cleanup_interruption is not None:
        raise cleanup_interruption
    return RunnerLaunchResult(state, exit_code, cgroup, child, tuple(events), failure,
                              observed_artifact_sha256, policy.policy_digest)
