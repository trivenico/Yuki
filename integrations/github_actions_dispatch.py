"""Owner-authorized, version-pinned GitHub Actions dispatch with durable deduplication."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
import re
import sqlite3
from typing import Callable
from uuid import uuid4

from automation.task_runtime import TaskRuntime
from core.sqlite_file_security import prepare_private_sqlite_path

_SHA = re.compile(r"^[a-f0-9]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


class GitHubActionsDispatchError(RuntimeError):
    """A task could not be safely dispatched to GitHub Actions."""


@dataclass(frozen=True, slots=True)
class GitHubActionsDispatchResult:
    accepted: bool
    repository: str
    workflow_id: int
    task_id: str
    task_version: int
    source_sha: str
    idempotency_key: str


class GitHubActionsDispatchLedger:
    """Durable one-dispatch-per-task-attempt fence.

    A dispatch whose network outcome is unknown is never automatically retried:
    the host must reconcile the GitHub run before an operator resets the ledger.
    """

    def __init__(self, database_path: str):
        self.path = prepare_private_sqlite_path(database_path)
        with closing(self._connect()) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS github_actions_dispatches (
                    owner_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    task_version INTEGER NOT NULL CHECK(task_version > 0),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    repository TEXT NOT NULL,
                    workflow_id INTEGER NOT NULL,
                    source_sha TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('dispatching','dispatched','outcome_unknown')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(owner_id, task_id, task_version)
                )
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(github_actions_dispatches)")}
            if "dispatch_id" not in columns:
                db.execute("ALTER TABLE github_actions_dispatches ADD COLUMN dispatch_id TEXT")
            if "completed_run_id" not in columns:
                db.execute("ALTER TABLE github_actions_dispatches ADD COLUMN completed_run_id INTEGER")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_github_actions_dispatch_id ON github_actions_dispatches(dispatch_id) WHERE dispatch_id IS NOT NULL")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def reserve(self, *, owner_id: str, task_id: str, task_version: int,
                idempotency_key: str, dispatch_id: str, repository: str, workflow_id: int,
                source_sha: str) -> bool:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("""
                INSERT OR IGNORE INTO github_actions_dispatches
                (owner_id, task_id, task_version, idempotency_key, repository,
                 workflow_id, source_sha, state, dispatch_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'dispatching', ?)
            """, (owner_id, task_id, task_version, idempotency_key,
                  repository, workflow_id, source_sha, dispatch_id))
            accepted = cursor.rowcount == 1
            db.commit()
            return accepted

    def verify_and_consume_completion(
        self, *, owner_id: str, task_id: str, task_version: int,
        dispatch_id: str, repository: str, workflow_id: int,
        source_sha: str, run_id: int,
    ) -> bool:
        """Bind exactly one completed run to a host-created dispatch reservation.

        Repeated webhook deliveries for the same run are idempotent; a second
        run cannot reuse the same correlation identifier.
        """
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT completed_run_id FROM github_actions_dispatches "
                "WHERE owner_id=? AND task_id=? AND task_version=? AND dispatch_id=? "
                "AND repository=? AND workflow_id=? AND source_sha=? AND state='dispatched'",
                (owner_id, task_id, task_version, dispatch_id, repository,
                 workflow_id, source_sha),
            ).fetchone()
            if row is None:
                db.rollback()
                return False
            bound_run = row[0]
            if bound_run is None:
                db.execute(
                    "UPDATE github_actions_dispatches SET completed_run_id=? "
                    "WHERE owner_id=? AND task_id=? AND task_version=? AND dispatch_id=? AND completed_run_id IS NULL",
                    (run_id, owner_id, task_id, task_version, dispatch_id),
                )
                accepted = db.execute("SELECT changes()").fetchone()[0] == 1
            else:
                accepted = bound_run == run_id
            db.commit()
            return accepted

    def settle(self, idempotency_key: str, state: str) -> None:
        if state not in {"dispatched", "outcome_unknown"}:
            raise ValueError("invalid dispatch settlement state.")
        with closing(self._connect()) as db:
            db.execute(
                "UPDATE github_actions_dispatches SET state=? WHERE idempotency_key=? AND state='dispatching'",
                (state, idempotency_key),
            )


class GitHubAppWorkflowDispatchClient:
    """Fixed-endpoint GitHub App adapter for workflow_dispatch.

    The token provider must mint/refresh an installation token scoped to the
    configured repository. The transport must use TLS verification, a bounded
    timeout, and must not follow redirects.
    """

    def __init__(self, *, repository: str, workflow_id: int,
                 installation_token_for_repository: Callable[[str], str],
                 http_post: Callable[..., int]):
        if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
            raise ValueError("repository must be an owner/name identifier.")
        if type(workflow_id) is not int or workflow_id < 1:
            raise ValueError("workflow_id must be a positive integer.")
        if not callable(installation_token_for_repository) or not callable(http_post):
            raise TypeError("GitHub App token provider and HTTP transport are required.")
        self.repository = repository
        self.workflow_id = workflow_id
        self.token_provider = installation_token_for_repository
        self.http_post = http_post

    def __call__(self, *, repository: str, workflow_id: int, ref: str,
                 inputs: dict[str, str], idempotency_key: str) -> None:
        if repository != self.repository or workflow_id != self.workflow_id:
            raise GitHubActionsDispatchError("GitHub App client is pinned to another repository or workflow.")
        if not isinstance(ref, str) or not _BRANCH.fullmatch(ref) or ".." in ref or "//" in ref:
            raise GitHubActionsDispatchError("workflow ref is invalid.")
        expected = {"yuki_task_id", "yuki_task_version", "yuki_source_sha", "yuki_dispatch_id"}
        if not isinstance(inputs, dict) or set(inputs) != expected or any(
            not isinstance(value, str) for value in inputs.values()
        ):
            raise GitHubActionsDispatchError("workflow dispatch inputs do not match the pinned contract.")
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", inputs["yuki_task_id"])
            or not re.fullmatch(r"[1-9][0-9]{0,8}", inputs["yuki_task_version"])
        ):
            raise GitHubActionsDispatchError("workflow task identity or version is invalid.")
        if not _SHA.fullmatch(inputs["yuki_source_sha"]):
            raise GitHubActionsDispatchError("workflow source SHA is invalid.")
        if not re.fullmatch(r"[a-f0-9]{32}", inputs["yuki_dispatch_id"]):
            raise GitHubActionsDispatchError("workflow dispatch correlation ID is invalid.")
        if not isinstance(idempotency_key, str) or not re.fullmatch(r"[a-f0-9]{64}", idempotency_key):
            raise GitHubActionsDispatchError("dispatch idempotency key is invalid.")
        try:
            token = self.token_provider(self.repository)
            if not isinstance(token, str) or not token or len(token) > 8192:
                raise ValueError("invalid installation token")
            url = (
                "https://api.github.com/repos/" + self.repository
                + "/actions/workflows/" + str(self.workflow_id) + "/dispatches"
            )
            status = self.http_post(
                url=url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": "Bearer " + token,
                    "X-GitHub-Api-Version": "2022-11-28",
                    "Content-Type": "application/json",
                },
                json_body={"ref": ref, "inputs": inputs},
                timeout_seconds=10,
                follow_redirects=False,
            )
        except Exception as exc:
            raise GitHubActionsDispatchError("GitHub App workflow dispatch request failed.") from exc
        if status != 204:
            raise GitHubActionsDispatchError("GitHub App workflow dispatch returned an unexpected status.")

class GitHubActionsTaskDispatcher:
    """Dispatch an exact task attempt through a trusted GitHub App client callback."""

    def __init__(
        self, *, repository: str, workflow_id: int, allowed_branches: set[str] | frozenset[str],
        task_runtime: TaskRuntime, ledger: GitHubActionsDispatchLedger,
        source_binding_resolver: Callable[[str, str], tuple[int, str, str] | None],
        workflow_dispatch: Callable[..., None],
    ):
        if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
            raise ValueError("repository must be an owner/name identifier.")
        if type(workflow_id) is not int or workflow_id < 1:
            raise ValueError("workflow_id must be a positive integer.")
        branches = frozenset(allowed_branches)
        if not branches or any(
            not isinstance(x, str) or not _BRANCH.fullmatch(x)
            or ".." in x or "//" in x or x.endswith("/")
            or x.endswith(".lock") or "@{" in x
            for x in branches
        ):
            raise ValueError("an explicit safe branch allowlist is required.")
        if not isinstance(task_runtime, TaskRuntime) or not isinstance(ledger, GitHubActionsDispatchLedger):
            raise TypeError("task_runtime and dispatch ledger are required.")
        if not callable(source_binding_resolver) or not callable(workflow_dispatch):
            raise TypeError("trusted source resolver and GitHub App dispatch callback are required.")
        self.repository = repository
        self.workflow_id = workflow_id
        self.branches = branches
        self.tasks = task_runtime
        self.ledger = ledger
        self.binding = source_binding_resolver
        self.dispatch_call = workflow_dispatch

    def dispatch(self, *, actor_id: str, owner_id: str, task_id: str) -> GitHubActionsDispatchResult:
        if actor_id != owner_id:
            raise PermissionError("only the task owner may dispatch CI.")
        if not self.tasks.is_authorized(actor_id, "task:ci:dispatch", task_id):
            raise PermissionError("task CI dispatch is not authorized.")
        task = self.tasks.get(actor_id=actor_id, task_id=task_id)
        if task.owner_id != owner_id or type(task.version) is not int or task.version < 1:
            raise GitHubActionsDispatchError("task ownership or version is invalid.")
        if task.state not in {"queued", "approved", "running", "succeeded"}:
            raise GitHubActionsDispatchError("task state is not eligible for CI verification.")
        try:
            binding = self.binding(owner_id, task_id)
        except Exception as exc:
            raise GitHubActionsDispatchError("task source binding could not be verified.") from exc
        if (
            not isinstance(binding, tuple) or len(binding) != 3
            or type(binding[0]) is not int or binding[0] != task.version
            or not isinstance(binding[1], str) or not _SHA.fullmatch(binding[1])
            or not isinstance(binding[2], str) or binding[2] not in self.branches
        ):
            raise GitHubActionsDispatchError("task version, source SHA, or branch binding is invalid.")
        source_sha, branch = binding[1], binding[2]
        dispatch_id = uuid4().hex
        key = sha256(
            (owner_id + "\0" + task_id + "\0" + str(task.version) + "\0" +
             self.repository + "\0" + str(self.workflow_id) + "\0" + source_sha).encode("utf-8")
        ).hexdigest()
        reserved = self.ledger.reserve(
            owner_id=owner_id, task_id=task_id, task_version=task.version,
            idempotency_key=key, dispatch_id=dispatch_id,
            repository=self.repository, workflow_id=self.workflow_id, source_sha=source_sha,
        )
        if not reserved:
            raise GitHubActionsDispatchError("this task attempt already has a dispatch reservation.")
        try:
            self.dispatch_call(
                repository=self.repository,
                workflow_id=self.workflow_id,
                ref=branch,
                inputs={
                    "yuki_task_id": task_id,
                    "yuki_task_version": str(task.version),
                    "yuki_source_sha": source_sha,
                    "yuki_dispatch_id": dispatch_id,
                },
                idempotency_key=key,
            )
        except Exception as exc:
            self.ledger.settle(key, "outcome_unknown")
            raise GitHubActionsDispatchError(
                "GitHub dispatch outcome is unknown; automatic retry is blocked pending reconciliation."
            ) from exc
        self.ledger.settle(key, "dispatched")
        return GitHubActionsDispatchResult(
            True, self.repository, self.workflow_id, task_id, task.version, source_sha, key,
        )
