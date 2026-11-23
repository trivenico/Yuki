"""Verify GitHub Actions test-report artifacts against authenticated run metadata."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import re
from typing import Callable, Mapping
from zipfile import BadZipFile, ZipFile

from core.task_evidence import EvidenceAttestation, TaskEvidenceStore

_GIT_SHA = re.compile(r"^[a-f0-9]{40}$")
_DIGEST = re.compile(r"^sha256:([a-f0-9]{64})$")
_REPORT_NAME = re.compile(r"^yuki-test-report-[1-9][0-9]*$")


class GitHubArtifactVerificationError(ValueError):
    """A GitHub Actions artifact could not be verified."""


@dataclass(frozen=True, slots=True)
class GitHubArtifactEvidenceResult:
    artifact_evidence_id: str
    report_evidence_id: str
    artifact_digest_sha256: str
    report_digest_sha256: str
    run_id: int
    task_id: str
    task_version: int
    test_count: int


class GitHubActionsArtifactVerifier:
    """Verify a run-bound unittest report using authenticated host GitHub clients.

    The callbacks must use a GitHub App installation token or another
    least-privilege credential. They must not accept caller-controlled URLs.
    """

    def __init__(
        self,
        *,
        evidence_store: TaskEvidenceStore,
        allowed_repositories: set[str] | frozenset[str],
        allowed_workflow_ids: set[int] | frozenset[int],
        owner_for_repository: Mapping[str, str],
        task_binding_resolver: Callable[[str, str], tuple[int, str] | None],
        workflow_run_lookup: Callable[[str, int], Mapping],
        artifact_lookup: Callable[[str, int, str], Mapping],
        artifact_download: Callable[[str, int], bytes],
        max_archive_bytes: int = 25_000_000,
        max_report_bytes: int = 10_000_000,
    ):
        if not isinstance(evidence_store, TaskEvidenceStore):
            raise TypeError("evidence_store must be a TaskEvidenceStore.")
        repositories = frozenset(allowed_repositories)
        workflows = frozenset(allowed_workflow_ids)
        if not repositories or any(not isinstance(x, str) or not x.strip() for x in repositories):
            raise ValueError("an explicit repository allowlist is required.")
        if not workflows or any(type(x) is not int or x < 1 for x in workflows):
            raise ValueError("an explicit workflow ID allowlist is required.")
        if set(owner_for_repository) != set(repositories) or any(
            not isinstance(owner, str) or not owner.strip() for owner in owner_for_repository.values()
        ):
            raise ValueError("each repository must map to one owner.")
        for callback in (task_binding_resolver, workflow_run_lookup, artifact_lookup, artifact_download):
            if not callable(callback):
                raise ValueError("all authenticated GitHub and task-binding callbacks are required.")
        if type(max_archive_bytes) is not int or not 1024 <= max_archive_bytes <= 100_000_000:
            raise ValueError("max_archive_bytes is outside the supported range.")
        if type(max_report_bytes) is not int or not 1024 <= max_report_bytes <= 25_000_000:
            raise ValueError("max_report_bytes is outside the supported range.")
        self._store = evidence_store
        self._repositories = repositories
        self._workflows = workflows
        self._owners = dict(owner_for_repository)
        self._binding = task_binding_resolver
        self._run_lookup = workflow_run_lookup
        self._artifact_lookup = artifact_lookup
        self._download = artifact_download
        self._max_archive = max_archive_bytes
        self._max_report = max_report_bytes

    def verify(
        self, *, repository: str, run_id: int, task_id: str, task_version: int,
    ) -> GitHubArtifactEvidenceResult:
        if repository not in self._repositories:
            raise GitHubArtifactVerificationError("repository is not allowlisted.")
        if type(run_id) is not int or run_id < 1:
            raise GitHubArtifactVerificationError("run_id must be a positive integer.")
        if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
            raise GitHubArtifactVerificationError("task_id is invalid.")
        if type(task_version) is not int or task_version < 1:
            raise GitHubArtifactVerificationError("task_version is invalid.")
        owner_id = self._owners[repository]
        try:
            binding = self._binding(owner_id, task_id)
            run = self._run_lookup(repository, run_id)
            artifact_name = "yuki-test-report-" + str(run_id)
            artifact = self._artifact_lookup(repository, run_id, artifact_name)
        except Exception as exc:
            raise GitHubArtifactVerificationError("authenticated GitHub or task lookup failed.") from exc
        if (
            not isinstance(binding, tuple) or len(binding) != 2
            or type(binding[0]) is not int or binding[0] != task_version
            or not isinstance(binding[1], str) or not _GIT_SHA.fullmatch(binding[1])
        ):
            raise GitHubArtifactVerificationError("task version or source binding is invalid.")
        if not isinstance(run, Mapping):
            raise GitHubArtifactVerificationError("workflow run metadata is invalid.")
        head_sha = run.get("head_sha")
        workflow_id = run.get("workflow_id")
        head_branch = run.get("head_branch")
        run_repository = run.get("repository_full_name")
        if run_repository is None and isinstance(run.get("repository"), Mapping):
            run_repository = run["repository"].get("full_name")
        if (
            type(run.get("id")) is not int or run["id"] != run_id
            or type(workflow_id) is not int or workflow_id not in self._workflows
            or run.get("conclusion") != "success"
            or not isinstance(head_sha, str) or not _GIT_SHA.fullmatch(head_sha)
            or head_sha != binding[1]
            or not isinstance(head_branch, str) or not head_branch
            or run_repository != repository
        ):
            raise GitHubArtifactVerificationError("workflow run identity, result, or source is not trusted.")
        if not isinstance(artifact, Mapping):
            raise GitHubArtifactVerificationError("artifact metadata is invalid.")
        artifact_id = artifact.get("id")
        digest_match = _DIGEST.fullmatch(artifact.get("digest", "")) if isinstance(artifact.get("digest"), str) else None
        artifact_run_id = artifact.get("workflow_run_id")
        if artifact_run_id is None and isinstance(artifact.get("workflow_run"), Mapping):
            artifact_run_id = artifact["workflow_run"].get("id")
        if (
            type(artifact_id) is not int or artifact_id < 1
            or artifact.get("name") != artifact_name
            or artifact.get("expired") is not False
            or artifact_run_id != run_id
            or digest_match is None
            or type(artifact.get("size_in_bytes")) is not int
            or not 1 <= artifact["size_in_bytes"] <= self._max_archive
        ):
            raise GitHubArtifactVerificationError("artifact metadata is expired, mismatched, or malformed.")
        try:
            archive = self._download(repository, artifact_id)
        except Exception as exc:
            raise GitHubArtifactVerificationError("artifact download failed.") from exc
        if (
            not isinstance(archive, bytes) or not archive
            or len(archive) > self._max_archive
            or len(archive) != artifact["size_in_bytes"]
        ):
            raise GitHubArtifactVerificationError("artifact archive size does not match its metadata or configured limit.")
        artifact_digest = sha256(archive).hexdigest()
        if artifact_digest != digest_match.group(1):
            raise GitHubArtifactVerificationError("artifact archive SHA-256 does not match GitHub metadata.")
        report = self._read_report(archive)
        report_digest = sha256(report).hexdigest()
        report_text = report.decode("utf-8", "replace")
        test_count_match = re.search(
            r"^Ran ([0-9]+) tests?(?: in [0-9]+(?:\\.[0-9]+)?s)?$",
            report_text, re.MULTILINE,
        )
        if test_count_match is None or not re.search(r"^OK(?: \\(.*\\))?$", report_text, re.MULTILINE):
            raise GitHubArtifactVerificationError("test report does not contain a successful unittest summary.")
        test_count = int(test_count_match.group(1))
        if test_count < 1:
            raise GitHubArtifactVerificationError("test report contains no executed tests.")
        run_url = "https://github.com/" + repository + "/actions/runs/" + str(run_id)
        existing = self._store.list_verified(
            owner_id=owner_id, task_id=task_id, task_version=task_version, limit=100,
        )
        if not any(item.kind == "ci_workflow" and item.reference == run_url for item in existing):
            raise GitHubArtifactVerificationError("a matching successful webhook attestation is required first.")
        artifact_url = run_url + "/artifacts/" + str(artifact_id)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        artifact_record = self._store.record_verified(EvidenceAttestation(
            owner_id=owner_id, task_id=task_id, task_version=task_version,
            kind="artifact", reference=artifact_url, digest_sha256=artifact_digest,
            verifier_id="github-actions-artifact:" + repository + ":" + str(workflow_id),
            verified_at=now,
        ))
        report_record = self._store.record_verified(EvidenceAttestation(
            owner_id=owner_id, task_id=task_id, task_version=task_version,
            kind="test_report", reference=artifact_url + "#yuki-test-results.log",
            digest_sha256=report_digest,
            verifier_id="github-actions-test-report:" + repository + ":" + str(workflow_id),
            verified_at=now,
        ))
        return GitHubArtifactEvidenceResult(
            artifact_record.evidence_id, report_record.evidence_id,
            artifact_digest, report_digest, run_id, task_id, task_version, test_count,
        )

    def _read_report(self, archive: bytes) -> bytes:
        try:
            with ZipFile(BytesIO(archive)) as zipped:
                candidates = [info for info in zipped.infolist() if info.filename == "yuki-test-results.log"]
                if len(zipped.infolist()) > 10 or sum(item.file_size for item in zipped.infolist()) > self._max_report * 2:
                    raise GitHubArtifactVerificationError("artifact archive contains too many or oversized entries.")
                if len(candidates) != 1:
                    raise GitHubArtifactVerificationError("archive must contain exactly one test report.")
                info = candidates[0]
                if info.is_dir() or info.file_size < 1 or info.file_size > self._max_report:
                    raise GitHubArtifactVerificationError("test report size is invalid.")
                if info.compress_size > self._max_archive:
                    raise GitHubArtifactVerificationError("compressed report exceeds its size limit.")
                return zipped.read(info)
        except BadZipFile as exc:
            raise GitHubArtifactVerificationError("artifact is not a valid ZIP archive.") from exc
