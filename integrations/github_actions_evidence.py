"""Authenticate GitHub Actions workflow_run webhooks before recording task evidence.

Configure an exact repository and workflow allowlist. Workflow run names must
include the explicit correlation token: [yuki-task:<task-id>:v<version>].
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
from typing import Mapping

from core.task_evidence import EvidenceAttestation, TaskEvidenceStore

_CORRELATION = re.compile(r"^\[yuki-task:([A-Za-z0-9][A-Za-z0-9._:-]{0,127}):v([1-9][0-9]{0,8})\]$")
_SHA256 = re.compile(r"^[a-f0-9]{40}$")


class GitHubWebhookError(ValueError):
    """A GitHub webhook failed authentication or policy validation."""


@dataclass(frozen=True, slots=True)
class GitHubActionsEvidenceResult:
    accepted: bool
    evidence_id: str | None
    task_id: str | None
    task_version: int | None
    reason: str


class GitHubActionsEvidenceWebhook:
    """Narrow GitHub Actions webhook adapter; no arbitrary event ingestion."""

    def __init__(
        self,
        *,
        webhook_secret: bytes,
        allowed_repositories: set[str] | frozenset[str],
        allowed_workflow_ids: set[int] | frozenset[int],
        evidence_store: TaskEvidenceStore,
        owner_for_repository: Mapping[str, str],
    ):
        if not isinstance(webhook_secret, bytes) or len(webhook_secret) < 32:
            raise ValueError("webhook_secret must contain at least 32 bytes.")
        repositories = frozenset(allowed_repositories)
        workflows = frozenset(allowed_workflow_ids)
        if not repositories or any(not isinstance(x, str) or not x.strip() or len(x) > 200 for x in repositories):
            raise ValueError("an explicit repository allowlist is required.")
        if not workflows or any(type(x) is not int or x < 1 for x in workflows):
            raise ValueError("an explicit positive workflow ID allowlist is required.")
        if not isinstance(evidence_store, TaskEvidenceStore):
            raise TypeError("evidence_store must be a TaskEvidenceStore.")
        if set(owner_for_repository) != set(repositories) or any(
            not isinstance(owner, str) or not owner.strip() or len(owner) > 200
            for owner in owner_for_repository.values()
        ):
            raise ValueError("each allowed repository must map to one owner.")
        self._secret = bytes(webhook_secret)
        self._repositories = repositories
        self._workflows = workflows
        self._store = evidence_store
        self._owners = dict(owner_for_repository)

    def handle(
        self, *, raw_body: bytes, signature_256: str,
        event_name: str, delivery_id: str,
    ) -> GitHubActionsEvidenceResult:
        if not isinstance(raw_body, bytes) or not raw_body or len(raw_body) > 1_000_000:
            raise GitHubWebhookError("webhook body is empty or exceeds 1 MB.")
        if event_name != "workflow_run":
            raise GitHubWebhookError("only workflow_run events are accepted.")
        if not isinstance(delivery_id, str) or not re.fullmatch(r"[A-Fa-f0-9-]{16,80}", delivery_id):
            raise GitHubWebhookError("invalid GitHub delivery identifier.")
        expected = "sha256=" + hmac.new(self._secret, raw_body, hashlib.sha256).hexdigest()
        if not isinstance(signature_256, str) or not hmac.compare_digest(expected, signature_256):
            raise GitHubWebhookError("webhook signature verification failed.")
        try:
            payload = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubWebhookError("webhook payload is not valid JSON.") from exc
        if not isinstance(payload, dict) or payload.get("action") != "completed":
            raise GitHubWebhookError("only completed workflow runs are accepted.")
        run = payload.get("workflow_run")
        repository = payload.get("repository")
        if not isinstance(run, dict) or not isinstance(repository, dict):
            raise GitHubWebhookError("workflow_run or repository payload is missing.")
        repository_name = repository.get("full_name")
        workflow_id = run.get("workflow_id")
        if repository_name not in self._repositories or workflow_id not in self._workflows:
            raise GitHubWebhookError("repository or workflow is not allowlisted.")
        if run.get("conclusion") != "success":
            return GitHubActionsEvidenceResult(False, None, None, None, "workflow_not_successful")
        run_name = run.get("display_title") or run.get("name")
        correlation = _CORRELATION.fullmatch(run_name) if isinstance(run_name, str) else None
        if correlation is None:
            raise GitHubWebhookError("workflow run name lacks the required task correlation token.")
        task_id, version_text = correlation.groups()
        task_version = int(version_text)
        head_sha = run.get("head_sha")
        run_id = run.get("id")
        html_url = run.get("html_url")
        if not isinstance(head_sha, str) or not _SHA256.fullmatch(head_sha):
            raise GitHubWebhookError("workflow run head SHA is invalid.")
        if type(run_id) is not int or run_id < 1:
            raise GitHubWebhookError("workflow run ID is invalid.")
        if not isinstance(html_url, str) or not html_url.startswith("https://github.com/"):
            raise GitHubWebhookError("workflow run URL is invalid.")
        if not html_url.startswith("https://github.com/" + repository_name + "/actions/runs/"):
            raise GitHubWebhookError("workflow run URL does not match the allowlisted repository.")
        digest = hashlib.sha256(raw_body).hexdigest()
        verifier_id = "github-actions-webhook:" + repository_name + ":" + str(workflow_id)
        from datetime import datetime, timezone
        attestation = EvidenceAttestation(
            owner_id=self._owners[repository_name],
            task_id=task_id,
            task_version=task_version,
            kind="ci_workflow",
            reference=html_url,
            digest_sha256=digest,
            verifier_id=verifier_id,
            verified_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        record = self._store.record_verified(attestation)
        return GitHubActionsEvidenceResult(
            True, record.evidence_id, task_id, task_version, "verified_workflow_success",
        )
