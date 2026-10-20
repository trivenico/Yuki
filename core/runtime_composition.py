"""Explicit runtime composition for manifest-dispatched skills.

Production gets a durable approval store but intentionally no registered
execution backend until verified OS sandboxing is available.
"""
from __future__ import annotations

import os
from pathlib import Path

from core.skill_dispatch import SkillDispatcher
from core.sqlite_action_approvals import SQLiteActionApprovalStore


def build_skill_dispatcher(workspace: str | os.PathLike[str], *, capability_grants=()) -> SkillDispatcher:
    root = Path(workspace).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise RuntimeError("Yuki workspace must not be a symbolic link for approval storage.")
    database_path = root / "action_approvals.sqlite3"
    store = SQLiteActionApprovalStore(str(database_path))
    dispatcher = SkillDispatcher(
        capability_grants=capability_grants,
        approval_store=store,
    )

    # Development/test can exercise the contract. Production registration is
    # deliberately withheld until the OS sandbox is independently verified.
    if os.environ.get("YUKI_ENV", "development").strip().lower() != "production":
        from core.builtin_skill_adapters import time_skill_command, time_skill_manifest
        from core.subprocess_executor import SubprocessExecutor

        dispatcher.register_subprocess(
            time_skill_manifest(),
            time_skill_command(),
            executor=SubprocessExecutor(timeout_seconds=5, max_output_bytes=8192),
        )
    return dispatcher
