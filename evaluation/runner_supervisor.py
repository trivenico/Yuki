"""Fail-closed ordering contract for a future isolated evaluation supervisor.

This is orchestration logic over an injected adapter, not a Linux sandbox. No
production adapter is provided here. A deployment must implement every adapter
operation with pinned descriptors, authenticated runner identity, kernel
isolation, bounded waits, and independently verified cleanup before enabling it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
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


class RunnerSupervisorAdapter(Protocol):
    """Narrow OS boundary. Implementations must not accept arbitrary shell text."""

    def create_cgroup(self, policy: RunnerIsolationPolicy) -> Any:
        """Return an owned handle, or fail atomically without leaving a cgroup."""
        ...
    def apply_policy(self, cgroup: Any, settings: dict[str, str]) -> None: ...
    def verify_policy(self, cgroup: Any, policy: RunnerIsolationPolicy) -> bool: ...
    def fork_blocked_child(self, cgroup: Any, artifact_descriptor: int) -> Any:
        """Return a handle to a blocked child, or self-abort/reap before raising.

        The adapter must not leak a child if creation partially succeeds but
        no handle is returned to the coordinator.
        """
        ...
    def place_child(self, child: Any, cgroup: Any) -> None: ...
    def verify_membership(self, child: Any, cgroup: Any) -> bool: ...
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


def run_supervised_attempt(
    *,
    adapter: RunnerSupervisorAdapter,
    policy: RunnerIsolationPolicy,
    artifact_descriptor: int,
) -> RunnerLaunchResult:
    """Run the required pre-exec sequence; any failed gate prevents exec release.

    The adapter owns descriptor pinning, child synchronization, process identity,
    timeout enforcement, output capture, and kernel operations. This function
    accepts only a pre-staged descriptor and a validated policy; it does not
    accept command strings, environment variables, or network settings.
    """
    if not isinstance(policy, RunnerIsolationPolicy) or not policy.verify():
        raise ValueError("runner isolation policy is invalid")
    if type(artifact_descriptor) is not int or artifact_descriptor < 0:
        raise ValueError("artifact descriptor must be a non-negative integer")

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

        child = adapter.fork_blocked_child(cgroup, artifact_descriptor)
        if child is None:
            raise RuntimeError("blocked_child_creation_failed")
        advance(RunnerLaunchState.CHILD_FORKED_BLOCKED)

        adapter.place_child(child, cgroup)
        advance(RunnerLaunchState.CHILD_PLACED)

        if adapter.verify_membership(child, cgroup) is not True:
            raise RuntimeError("child_membership_verification_failed")
        advance(RunnerLaunchState.MEMBERSHIP_VERIFIED)

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
                cleanup_interruption = cleanup_interruption or cleanup_exc
                failure = (failure + ";" if failure else "") + "cgroup_cleanup_unverified"
                if state is not RunnerLaunchState.FAILED:
                    advance(RunnerLaunchState.FAILED)

    if state is not RunnerLaunchState.FAILED:
        advance(RunnerLaunchState.CLEANED)
    if cleanup_interruption is not None:
        raise cleanup_interruption
    return RunnerLaunchResult(state, exit_code, cgroup, child, tuple(events), failure)
