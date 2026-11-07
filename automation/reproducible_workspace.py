"""Bind pinned Git source snapshots to durable artifacts and workspaces.

This manager captures a commit, stores a deterministic byte bundle as an
engineering artifact, records source/dependency provenance, and materializes
the exact snapshot into a fresh workspace. It does not execute project code.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import base64
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Mapping

from automation.git_repository_snapshot import GitRepositorySnapshotReader
from automation.repository_snapshot import RepositorySnapshotBuilder
from automation.verification_workflow import VerificationWorkflow, ArtifactRecord

_LOCKFILES = frozenset({
    "requirements.txt", "requirements-dev.txt", "Pipfile.lock",
    "poetry.lock", "uv.lock", "pyproject.toml", "package-lock.json",
    "pnpm-lock.yaml", "yarn.lock", "Cargo.lock", "go.sum",
    "Gemfile.lock", "composer.lock",
})


class ReproducibleWorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkspaceProvenance:
    artifact_id: str
    artifact_sha256: str
    source_commit: str
    snapshot_sha256: str
    dependency_manifest_sha256: str
    dependency_files: tuple[str, ...]
    workspace_path: str
    file_count: int
    total_bytes: int


class GitBackedWorkspaceManager:
    """Capture a pinned Git commit as an artifact and materialize its exact bytes."""

    def __init__(
        self,
        repository: str | Path,
        workspace_root: str | Path,
        database_path: str | Path,
        snapshot_reader: GitRepositorySnapshotReader,
        verification_workflow: VerificationWorkflow,
    ):
        self.repository = Path(repository).expanduser().resolve(strict=True)
        self.workspace_root = Path(workspace_root).expanduser().absolute()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.database_path = str(database_path)
        if not self.database_path.strip():
            raise ValueError("database_path_required")
        if not isinstance(snapshot_reader, GitRepositorySnapshotReader):
            raise TypeError("snapshot_reader_required")
        if not isinstance(verification_workflow, VerificationWorkflow):
            raise TypeError("verification_workflow_required")
        self.snapshot_reader = snapshot_reader
        self.verification_workflow = verification_workflow
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS reproducible_workspaces (
                    artifact_id TEXT PRIMARY KEY,
                    artifact_sha256 TEXT NOT NULL,
                    source_commit TEXT NOT NULL,
                    snapshot_sha256 TEXT NOT NULL,
                    dependency_manifest_sha256 TEXT NOT NULL,
                    dependency_files_json TEXT NOT NULL,
                    workspace_path TEXT NOT NULL,
                    file_count INTEGER NOT NULL,
                    total_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _bundle(files: Mapping[str, bytes]) -> bytes:
        payload = {
            "format": "yuki-source-bundle-v1",
            "files": [
                {"path": path, "content_base64": base64.b64encode(content).decode("ascii")}
                for path, content in sorted(files.items())
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _dependency_manifest(files: Mapping[str, bytes]) -> tuple[tuple[str, ...], str]:
        selected = [
            (path, content) for path, content in files.items()
            if Path(path).name in _LOCKFILES
        ]
        selected.sort(key=lambda item: item[0])
        canonical = json.dumps(
            [{"path": path, "sha256": hashlib.sha256(content).hexdigest(),
              "byte_length": len(content)} for path, content in selected],
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return tuple(path for path, _ in selected), hashlib.sha256(
            b"yuki-dependency-manifest-v1\\0" + canonical
        ).hexdigest()

    def capture(
        self, *, commit: str, plan_id: str, subtask_id: str, task_id: str,
        author_agent_id: str, created_at: str, workspace_name: str,
    ) -> WorkspaceProvenance:
        if not isinstance(workspace_name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", workspace_name):
            raise ValueError("invalid_workspace_name")
        git_snapshot = self.snapshot_reader.read_commit(self.repository, commit)
        files = git_snapshot.files
        bundle = self._bundle(files)
        artifact: ArtifactRecord = self.verification_workflow.record_artifact(
            plan_id=plan_id, subtask_id=subtask_id, task_id=task_id,
            author_agent_id=author_agent_id, content=bundle, created_at=created_at,
        )
        dependency_files, dependency_digest = self._dependency_manifest(files)
        destination = self.workspace_root / workspace_name
        builder: RepositorySnapshotBuilder = self.snapshot_reader.builder
        builder.materialize(
            files, destination,
            expected_snapshot_sha256=git_snapshot.snapshot.snapshot_sha256,
        )
        provenance = WorkspaceProvenance(
            artifact_id=artifact.artifact_id,
            artifact_sha256=artifact.artifact_sha256,
            source_commit=git_snapshot.commit_sha,
            snapshot_sha256=git_snapshot.snapshot.snapshot_sha256,
            dependency_manifest_sha256=dependency_digest,
            dependency_files=dependency_files,
            workspace_path=str(destination.resolve()),
            file_count=len(git_snapshot.snapshot.files),
            total_bytes=git_snapshot.snapshot.total_bytes,
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM reproducible_workspaces WHERE artifact_id=?",
                (artifact.artifact_id,),
            ).fetchone()
            values = (
                provenance.artifact_id, provenance.artifact_sha256,
                provenance.source_commit, provenance.snapshot_sha256,
                provenance.dependency_manifest_sha256,
                json.dumps(provenance.dependency_files, separators=(",", ":")),
                provenance.workspace_path, provenance.file_count,
                provenance.total_bytes, created_at,
            )
            if existing is not None:
                expected = dict(zip(
                    ("artifact_id", "artifact_sha256", "source_commit", "snapshot_sha256",
                     "dependency_manifest_sha256", "dependency_files_json", "workspace_path",
                     "file_count", "total_bytes", "created_at"), values,
                ))
                if any(existing[key] != value for key, value in expected.items()):
                    raise ReproducibleWorkspaceError("workspace_provenance_conflict")
            else:
                db.execute(
                    """INSERT INTO reproducible_workspaces VALUES
                       (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values,
                )
        return provenance

    def get_provenance(self, artifact_id: str) -> WorkspaceProvenance:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM reproducible_workspaces WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise KeyError("workspace provenance not found")
        return WorkspaceProvenance(
            artifact_id=row["artifact_id"],
            artifact_sha256=row["artifact_sha256"],
            source_commit=row["source_commit"],
            snapshot_sha256=row["snapshot_sha256"],
            dependency_manifest_sha256=row["dependency_manifest_sha256"],
            dependency_files=tuple(json.loads(row["dependency_files_json"])),
            workspace_path=row["workspace_path"],
            file_count=row["file_count"],
            total_bytes=row["total_bytes"],
        )
