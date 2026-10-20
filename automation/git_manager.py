"""Safe Git operation planner. This module does not execute Git commands."""

from pathlib import Path


class GitManager:
    ALLOWED = {"status", "diff", "test", "commit", "push", "publish"}

    def __init__(self, workspace):
        self.workspace = Path(workspace).expanduser().resolve()

    def plan(self, action, *, message=""):
        if action not in self.ALLOWED:
            raise ValueError("Unsupported Git action.")
        if not self.workspace.exists() or not self.workspace.is_dir():
            raise ValueError("Workspace does not exist.")
        if action in {"commit", "push", "publish"} and not message.strip():
            raise ValueError("A specific change/release summary is required.")
        return {
            "action": action,
            "workspace": str(self.workspace),
            "requires_approval": action in {"commit", "push", "publish"},
            "execution": "not_implemented",
            "message": "Plan only; no Git command was executed.",
        }
