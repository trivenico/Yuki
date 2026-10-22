"""Execute approved coding-task checks and persist non-secret evidence."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib, json, secrets, sqlite3
from pathlib import Path
from typing import Callable, FrozenSet
from core.coding_task_runner import SandboxExecutionError, SandboxExecutionRequest, SandboxExecutionResult, run_sandboxed_task
from core.coding_tasks import CodingTaskError, CodingTaskStore
from core.sandbox_policy import SandboxPolicy

class CodingTaskExecutionError(RuntimeError):
    """Execution request failed validation or execution."""

@dataclass(frozen=True)
class CodingTaskExecutionEvidence:
    run_id: str
    task_id: str
    patch_sha256: str
    command_sha256: str
    output_sha256: str
    output_bytes: int
    returncode: int
    timed_out: bool
    output_truncated: bool
    duration_seconds: float
    created_at: str

def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

class CodingTaskExecutionStore:
    """SQLite evidence store that deliberately never persists raw logs."""
    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path, timeout=5.0) as db:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("""CREATE TABLE IF NOT EXISTS coding_task_execution_evidence (
                run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, patch_sha256 TEXT NOT NULL,
                command_sha256 TEXT NOT NULL, output_sha256 TEXT NOT NULL, output_bytes INTEGER NOT NULL,
                returncode INTEGER NOT NULL, timed_out INTEGER NOT NULL, output_truncated INTEGER NOT NULL,
                duration_seconds REAL NOT NULL, created_at TEXT NOT NULL)""")

    def record(self, e: CodingTaskExecutionEvidence) -> None:
        with sqlite3.connect(self.database_path, timeout=5.0) as db:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("INSERT INTO coding_task_execution_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (e.run_id,e.task_id,e.patch_sha256,e.command_sha256,e.output_sha256,e.output_bytes,
                 e.returncode,int(e.timed_out),int(e.output_truncated),e.duration_seconds,e.created_at))

    def get(self, run_id: str) -> CodingTaskExecutionEvidence:
        with sqlite3.connect(self.database_path, timeout=5.0) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM coding_task_execution_evidence WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError("Execution evidence not found")
        return CodingTaskExecutionEvidence(row["run_id"],row["task_id"],row["patch_sha256"],
            row["command_sha256"],row["output_sha256"],row["output_bytes"],row["returncode"],
            bool(row["timed_out"]),bool(row["output_truncated"]),row["duration_seconds"],row["created_at"])

def execute_approved_coding_task(
    task_store: CodingTaskStore, evidence_store: CodingTaskExecutionStore, policy: SandboxPolicy, *,
    task_id: str, image: str, workspace: str, command: list[str] | tuple[str, ...],
    approve: Callable[[str, str, SandboxExecutionRequest], bool],
    allowed_executables: FrozenSet[str] = frozenset({"python", "python3", "pytest", "ruff", "mypy"}),
) -> tuple[SandboxExecutionResult, CodingTaskExecutionEvidence]:
    """Run a bounded check; approval is bound to task, patch digest, and exact request."""
    try:
        task = task_store.get(task_id)
    except (CodingTaskError, KeyError, ValueError) as exc:
        raise CodingTaskExecutionError("coding task does not exist or is invalid") from exc
    if task.state != "approved":
        raise CodingTaskExecutionError("task must be explicitly approved before execution")
    if not task.patch_sha256 or not task_store.patch_integrity_ok(task_id):
        raise CodingTaskExecutionError("approved task must have an intact reviewed patch")
    task_workspace, requested_workspace = str(Path(task.workspace).resolve()), str(Path(workspace).resolve())
    if task_workspace != requested_workspace:
        raise CodingTaskExecutionError("execution workspace differs from reviewed task workspace")
    if not isinstance(command, (list, tuple)) or not command or any(
        not isinstance(part, str) or not part or "\x00" in part for part in command
    ):
        raise CodingTaskExecutionError("command must be a non-empty argv list of NUL-free strings")
    executable = Path(command[0]).name
    if executable not in allowed_executables or command[0] != executable:
        raise CodingTaskExecutionError("command executable is not in the configured allowlist")
    if not callable(approve):
        raise CodingTaskExecutionError("trusted approval adapter is required")
    def bound_approval(request: SandboxExecutionRequest) -> bool:
        return approve(task_id, task.patch_sha256, request)
    try:
        result = run_sandboxed_task(policy, image=image, workspace=requested_workspace,
                                    command=command, approve=bound_approval)
    except SandboxExecutionError as exc:
        raise CodingTaskExecutionError("sandbox execution denied or failed") from exc
    output = result.output.encode("utf-8")
    evidence = CodingTaskExecutionEvidence(
        secrets.token_urlsafe(18), task_id, task.patch_sha256,
        _digest(json.dumps(list(command), ensure_ascii=True).encode("utf-8")),
        _digest(output), len(output), result.returncode, result.timed_out,
        result.output_truncated, result.duration_seconds, datetime.now(timezone.utc).isoformat())
    try:
        evidence_store.record(evidence)
    except sqlite3.Error as exc:
        raise CodingTaskExecutionError("execution evidence persistence failed") from exc
    return result, evidence
