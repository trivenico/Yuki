"""Opt-in autonomous validation mode for disposable self-upgrade test workspaces.

This deliberately does not enable unattended production promotion. Automatic
approvals are scoped to IsolatedUpgradeCoordinator's temporary copy, and test
execution must be delegated to an explicitly supplied isolated runner.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Sequence

from automation.authorized_read_tools import ToolPrincipal, ToolAuthorizationError
from automation.approved_workspace_changes import FileChange
from automation.self_upgrade_coordinator import IsolatedUpgradeCoordinator
from automation.isolated_upgrade_worker import PodmanIsolatedTestRunner

_ENABLE_FLAG = "YUKI_AUTONOMOUS_UPGRADE_TEST_MODE"


def build_autonomous_upgrade_test_coordinator(
    workspace: str | Path,
    *,
    trusted_principal: Callable[[ToolPrincipal], bool],
    audit: Callable[[dict], None],
    allowed_paths: Sequence[str],
    allowed_test_modules: Sequence[str],
    isolated_runner_factory: Callable[[Path], object] | None = None,
    environ: dict[str, str] | None = None,
) -> IsolatedUpgradeCoordinator:
    """Build test-only coordinator when explicitly opted in and isolation is provided.

    This mode auto-approves digest-bound edits and test runs only inside the
    coordinator's disposable staging directory. It never writes changes back to
    the original workspace, runs tests on the host, or authorizes promotion.
    """
    env = os.environ if environ is None else environ
    if env.get(_ENABLE_FLAG) != "1":
        raise ToolAuthorizationError(
            f"autonomous upgrade test mode is disabled; set {_ENABLE_FLAG}=1 to opt in."
        )
    if not callable(trusted_principal) or not callable(audit):
        raise TypeError("trusted principal validation and audit callbacks are required.")
    if isolated_runner_factory is not None and not callable(isolated_runner_factory):
        raise ToolAuthorizationError(
            "isolated_runner_factory must be callable; host execution is forbidden."
        )

    # Prefer the real rootless Podman adapter by default. An injected factory is
    # retained for unit tests and controlled deployments with an audited adapter.
    image = env.get("YUKI_TEST_WORKER_IMAGE", "").strip()
    if isolated_runner_factory is None:
        import re

        if not re.fullmatch(
            r"docker\.io/library/python:3\.11-slim@sha256:[0-9a-f]{64}", image
        ):
            raise ToolAuthorizationError(
                "YUKI_TEST_WORKER_IMAGE must be the approved official Python 3.11 slim image pinned by a lowercase sha256 digest."
            )

        def isolated_runner_factory(staged_root: Path) -> object:
            return PodmanIsolatedTestRunner(
                staged_root,
                image=image,
                trusted_principal=trusted_principal,
                approve_run=approve_staged_test,
                audit=audit_test_mode,
                allowed_test_modules=allowed_test_modules,
            )

    def approve_staged_change(actor: str, change_id: str, digest: str) -> bool:
        # The coordinator applies this only to its disposable staged workspace.
        return bool(actor and change_id.startswith("chg_") and len(digest) == 64)

    def approve_staged_test(actor: str, run_id: str, digest: str) -> bool:
        # The runner remains constrained by the configured module allow-list.
        return bool(actor and run_id.startswith("isolated_test_") and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest))

    def audit_test_mode(event: dict) -> None:
        enriched = dict(event)
        enriched["execution_mode"] = "autonomous_isolated_test"
        enriched["promotion_allowed"] = False
        audit(enriched)

    return IsolatedUpgradeCoordinator(
        workspace,
        trusted_principal=trusted_principal,
        approve_change=approve_staged_change,
        approve_test=approve_staged_test,
        audit=audit_test_mode,
        allowed_paths=allowed_paths,
        allowed_test_modules=allowed_test_modules,
        allow_host_test_execution=False,
        isolated_runner_factory=isolated_runner_factory,
    )
