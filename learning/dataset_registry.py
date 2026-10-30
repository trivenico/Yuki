"""Curated metadata registry and approval gate for external training datasets.

Discovery never downloads data or grants training permission. A dataset becomes
eligible only after its exact upstream revision, license evidence, provenance,
privacy review and human approval are recorded.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Iterable

_REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_LICENSE = re.compile(r"^[A-Za-z0-9.+-]{2,80}$")
_ALLOWED_LICENSES = frozenset({
    "Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "CC0-1.0",
    "CC-BY-4.0",
})

# Candidates are discovery leads only. License values are intentionally not
# asserted here: verify the exact dataset card and files at the pinned revision.
_STARTER_CANDIDATES = (
    {"repo_id":"OpenAssistant/oasst1","tasks":["instruction_following","dialogue"],"notes":"Human-generated assistant conversation dataset; inspect privacy, provenance and exact terms."},
    {"repo_id":"openai/gsm8k","tasks":["math_reasoning"],"notes":"Grade-school math reasoning benchmark; verify dataset license and split use."},
    {"repo_id":"google-research-datasets/mbpp","tasks":["code_generation"],"notes":"Python programming problems benchmark; verify terms and contamination risk."},
    {"repo_id":"truthfulqa/truthful_qa","tasks":["truthfulness","evaluation"],"notes":"Truthfulness evaluation resource; verify task-specific terms and permitted use."},
    {"repo_id":"HuggingFaceH4/ultrafeedback_binarized","tasks":["preference_learning"],"notes":"Preference dataset; audit upstream source lineage and all component licenses."},
)

@dataclass(frozen=True)
class DatasetApproval:
    repo_id: str
    revision: str
    license_id: str
    license_evidence_url: str
    provenance_reviewed: bool
    privacy_reviewed: bool
    security_reviewed: bool
    human_approved: bool
    approved_by: str

    def validate(self) -> None:
        if not isinstance(self.repo_id, str) or not _REPO_ID.fullmatch(self.repo_id):
            raise ValueError("repo_id must be an exact Hugging Face owner/name identifier.")
        if not isinstance(self.revision, str) or not _REVISION.fullmatch(self.revision):
            raise ValueError("A pinned 40-character commit revision is required.")
        if not isinstance(self.license_id, str) or not _LICENSE.fullmatch(self.license_id):
            raise ValueError("A valid SPDX-style license identifier is required.")
        if self.license_id not in _ALLOWED_LICENSES:
            raise PermissionError("License is not in Yuki's initial training allowlist; manual legal review is required.")
        if not isinstance(self.license_evidence_url, str) or not self.license_evidence_url.startswith("https://huggingface.co/datasets/"):
            raise ValueError("An HTTPS dataset-card evidence URL is required.")
        if not all(type(value) is bool and value for value in (
            self.provenance_reviewed, self.privacy_reviewed,
            self.security_reviewed, self.human_approved,
        )):
            raise PermissionError("Provenance, privacy, security and human approval gates must all pass.")
        if not isinstance(self.approved_by, str) or not self.approved_by.strip() or len(self.approved_by) > 128:
            raise ValueError("A named reviewer is required.")

    def to_dict(self):
        self.validate()
        return asdict(self)


class DatasetRegistry:
    """In-memory discovery catalogue and strict explicit-approval gate."""
    def __init__(self, candidates: Iterable[dict] = _STARTER_CANDIDATES):
        self._candidates = tuple(dict(item) for item in candidates)
        self._approvals: dict[tuple[str, str], DatasetApproval] = {}

    def candidates(self) -> list[dict]:
        return [
            {**item, "source":"Hugging Face Datasets",
             "status":"candidate_only_license_and_safety_review_required",
             "automatic_download":False, "training_eligible":False}
            for item in self._candidates
        ]

    def approve(self, approval: DatasetApproval) -> dict:
        if not isinstance(approval, DatasetApproval):
            raise TypeError("approval must be a DatasetApproval.")
        approval.validate()
        if not any(item["repo_id"] == approval.repo_id for item in self._candidates):
            raise ValueError("Dataset is not in the reviewed candidate catalogue.")
        key = (approval.repo_id, approval.revision)
        self._approvals[key] = approval
        return {"repo_id":approval.repo_id, "revision":approval.revision,
                "status":"approved_for_separate_ingestion_review",
                "training_eligible":True, "automatic_download":False}

    def is_approved(self, repo_id: str, revision: str) -> bool:
        return (repo_id, revision) in self._approvals

    def approved_records(self) -> list[dict]:
        return [item.to_dict() for item in self._approvals.values()]
