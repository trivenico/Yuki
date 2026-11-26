"""Canonical composition for the private SQLite control-plane stores.

This factory is the supported way to construct the authentication and workflow
stores together. It creates one maintenance-fence authority and injects that
same instance into both stores before either store initializes its schema.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from automation.workflow_store import WorkflowRunStore
from core.auth import BuiltInAuthService
from core.maintenance_fence import MaintenanceFence
from core.portable_paths import private_profile_root
from core.private_profile import ensure_private_profile_directory


class ControlPlaneCompositionError(RuntimeError):
    """The private control-plane stores could not be safely composed."""


@dataclass(frozen=True)
class ControlPlaneStores:
    """Stores sharing one maintenance authority and one private profile root."""

    auth: BuiltInAuthService
    workflows: WorkflowRunStore
    maintenance_fence: MaintenanceFence
    profile_root: Path


def _database_filename(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or Path(value).name != value
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError(f"{field} must be a simple database filename.")
    if not value.endswith(".sqlite3"):
        raise ValueError(f"{field} must use the .sqlite3 suffix.")
    return value


def build_control_plane_stores(
    *,
    profile_root: str | Path | None = None,
    auth_database_name: str = "auth.sqlite3",
    workflow_database_name: str = "workflows.sqlite3",
    auth_options: Mapping[str, Any] | None = None,
    workflow_options: Mapping[str, Any] | None = None,
    maintenance_timeout_seconds: float = 10.0,
) -> ControlPlaneStores:
    """Build auth and workflow stores with one mandatory shared fence.

    This factory intentionally fails closed on platforms where the private
    profile validator cannot verify directory ACLs. Database paths are fixed
    beneath the validated private root; callers cannot inject a different
    fence through per-store options.
    """
    auth_name = _database_filename(auth_database_name, "auth_database_name")
    workflow_name = _database_filename(workflow_database_name, "workflow_database_name")
    if auth_name == workflow_name:
        raise ValueError("auth and workflow databases must have distinct filenames.")
    if auth_options is not None and not isinstance(auth_options, Mapping):
        raise TypeError("auth_options must be a mapping.")
    if workflow_options is not None and not isinstance(workflow_options, Mapping):
        raise TypeError("workflow_options must be a mapping.")

    auth_kwargs = dict(auth_options or {})
    workflow_kwargs = dict(workflow_options or {})
    if "maintenance_fence" in auth_kwargs or "maintenance_fence" in workflow_kwargs:
        raise ValueError("maintenance_fence is owned by the control-plane composition.")
    if "database_path" in auth_kwargs or "database_path" in workflow_kwargs:
        raise ValueError("database_path is owned by the control-plane composition.")

    requested_root = private_profile_root() if profile_root is None else Path(profile_root)
    try:
        root = ensure_private_profile_directory(requested_root)
        fence = MaintenanceFence(root, timeout_seconds=maintenance_timeout_seconds)
        auth = BuiltInAuthService(str(root / auth_name), maintenance_fence=fence, **auth_kwargs)
        workflows = WorkflowRunStore(str(root / workflow_name), maintenance_fence=fence, **workflow_kwargs)
    except Exception as exc:
        raise ControlPlaneCompositionError(
            "The private control-plane stores could not be initialized safely."
        ) from exc

    return ControlPlaneStores(
        auth=auth,
        workflows=workflows,
        maintenance_fence=fence,
        profile_root=root,
    )
