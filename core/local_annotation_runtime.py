"""Secure local composition for the installed CLI annotation workbench."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.data_annotation import SQLiteAnnotationWorkbench
from core.local_owner_identity import load_or_create_local_owner_id


@dataclass(frozen=True)
class LocalAnnotationRuntime:
    """Owner-scoped local annotation store; SQLite connections are operation-scoped."""

    workbench: SQLiteAnnotationWorkbench
    owner_id: str


def build_local_annotation_runtime(
    config: dict,
    *,
    owner_id_factory: Callable[[], str] | None = None,
    workbench_factory: Callable[[str], SQLiteAnnotationWorkbench] = SQLiteAnnotationWorkbench,
) -> LocalAnnotationRuntime:
    """Create a private annotation database beneath the configured workspace.

    The local owner ID is the same stable identity used by Yuki's local CLI
    conversations. This identity is strictly local and is not an API credential.
    """
    if not isinstance(config, dict):
        raise TypeError("config_must_be_mapping")
    workspace_value = config.get("workspace")
    if not isinstance(workspace_value, str) or not workspace_value.strip():
        raise ValueError("annotation_workspace_required")
    workspace = Path(workspace_value).expanduser()
    if not workspace.is_absolute():
        raise ValueError("annotation_workspace_must_be_absolute")
    workspace.mkdir(parents=True, exist_ok=True)
    if workspace.is_symlink() or not workspace.is_dir():
        raise ValueError("annotation_workspace_must_be_directory")

    data_dir = workspace / "annotation"
    if data_dir.is_symlink():
        raise ValueError("annotation_data_directory_must_not_be_symlink")
    data_dir.mkdir(mode=0o700, exist_ok=True)
    if data_dir.is_symlink() or not data_dir.is_dir():
        raise ValueError("annotation_data_directory_must_be_directory")
    if os.name == "posix":
        os.chmod(data_dir, 0o700)

    database_path = data_dir / "workbench.sqlite3"
    if database_path.is_symlink():
        raise ValueError("annotation_database_must_not_be_symlink")
    owner_id = (
        owner_id_factory() if owner_id_factory is not None
        else load_or_create_local_owner_id(workspace)
    )
    if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 128:
        raise ValueError("annotation_owner_identity_invalid")
    workbench = workbench_factory(str(database_path))
    return LocalAnnotationRuntime(workbench=workbench, owner_id=owner_id)
