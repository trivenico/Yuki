"""Reviewed model capabilities and transparent task classification.

This module only classifies intent and filters model metadata. It does not
invoke models, change active providers, dispatch jobs, or infer unverified
capabilities from model names.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import re
from typing import Iterable

CAPABILITIES = frozenset({
    "reasoning", "coding", "vision", "embeddings",
    "transcription", "speech_generation", "tool_use",
})
TASK_TYPES = frozenset({
    "general", "reasoning", "coding", "vision", "embeddings",
    "transcription", "speech_generation", "tool_use",
})
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")

@dataclass(frozen=True)
class ModelCapability:
    model_id: str
    capabilities: frozenset[str]
    evidence: str
    verified: bool = False
    runtime_status: str = "unknown"
    model_digest: str | None = None

    def __post_init__(self):
        if not isinstance(self.model_id, str) or not _MODEL_ID.fullmatch(self.model_id):
            raise ValueError("invalid_model_id")
        if not isinstance(self.capabilities, (set, frozenset)) or not self.capabilities:
            raise ValueError("capabilities must be a non-empty set")
        if not self.capabilities.issubset(CAPABILITIES):
            raise ValueError("unsupported_model_capability")
        if not isinstance(self.evidence, str) or not self.evidence.strip() or len(self.evidence) > 500:
            raise ValueError("capability_evidence_required")
        if type(self.verified) is not bool:
            raise ValueError("verified must be boolean")
        if self.runtime_status not in {"verified", "incompatible", "unknown"}:
            raise ValueError("invalid_runtime_status")
        if self.model_digest is not None and (not isinstance(self.model_digest, str) or not re.fullmatch(r"[A-Fa-f0-9]{32,128}", self.model_digest)):
            raise ValueError("invalid_model_digest")

    def to_dict(self):
        value = asdict(self)
        value["capabilities"] = sorted(self.capabilities)
        return value

class ModelCapabilityRegistry:
    """In-memory registry of explicitly reviewed model capability claims."""
    def __init__(self, records: Iterable[ModelCapability] = ()):
        self._records = {}
        for record in records:
            self.register(record)

    def register(self, record: ModelCapability):
        if not isinstance(record, ModelCapability):
            raise TypeError("record must be ModelCapability")
        self._records[record.model_id] = record

    def get(self, model_id: str):
        return self._records.get(model_id)

    def find(self, required: Iterable[str], *, verified_only: bool = True):
        required = frozenset(required)
        if not required.issubset(CAPABILITIES):
            raise ValueError("unsupported_required_capability")
        matches = []
        for item in self._records.values():
            if verified_only and (not item.verified or item.runtime_status != "verified"):
                continue
            if required.issubset(item.capabilities):
                matches.append(item)
        return sorted(matches, key=lambda item: item.model_id)

# Deliberately conservative phrase patterns. The classifier exposes matched
# categories and confidence so callers can request clarification when unclear.
_PATTERNS = {
    "coding": (
        r"\b(write|debug|review|refactor|implement|fix|explain)\b.{0,50}\b(code|python|javascript|typescript|sql|function|class|api|bug)\b",
        r"\b(code|program|software|script|repository|pull request|unit test|compile)\b",
    ),
    "vision": (
        r"\b(look at|describe|analyze|read|inspect|extract text from)\b.{0,50}\b(image|photo|picture|screenshot|diagram)\b",
        r"\b(image|photo|picture|screenshot|visual)\b",
    ),
    "embeddings": (
        r"\b(embedding|vectorize|semantic search|similarity search)\b",
    ),
    "transcription": (
        r"\b(transcribe|speech to text|audio transcription|what is said in this audio)\b",
    ),
    "speech_generation": (
        r"\b(text to speech|generate speech|speak this|voice this|audio narration)\b",
    ),
    "tool_use": (
        r"\b(call (the )?api|use (the )?tool|run (the )?command|execute (the )?workflow|browse the web|search the web)\b",
    ),
    "reasoning": (
        r"\b(reason|prove|derive|compare|evaluate|plan|solve|analyze|investigate|architect)\b",
        r"\b(step by step|trade[- ]offs|root cause|design a system)\b",
    ),
}

def classify_task(text: str, *, explicit_task_type: str | None = None):
    """Return an explainable task classification; never execute the request."""
    if not isinstance(text, str) or not text.strip() or len(text) > 50_000:
        raise ValueError("task_text_must_be_nonempty_and_bounded")
    if explicit_task_type is not None:
        if explicit_task_type not in TASK_TYPES:
            raise ValueError("unsupported_explicit_task_type")
        required = [] if explicit_task_type == "general" else [explicit_task_type]
        return {
            "task_type": explicit_task_type,
            "required_capabilities": required,
            "confidence": 1.0,
            "matched_rules": ["caller_explicit_task_type"],
            "needs_clarification": False,
            "automatic_execution": False,
        }
    normalized = text.lower()
    matches = []
    for task_type, patterns in _PATTERNS.items():
        if any(re.search(pattern, normalized) for pattern in patterns):
            matches.append(task_type)
    if not matches:
        return {
            "task_type": "general",
            "required_capabilities": ["reasoning"],
            "confidence": 0.35,
            "matched_rules": [],
            "needs_clarification": True,
            "automatic_execution": False,
        }
    # Multiple matched capabilities are retained rather than forcing a
    # single label (e.g. inspect screenshot and fix the code shown).
    confidence = 0.75 if len(matches) == 1 else 0.65
    primary = matches[0] if len(matches) == 1 else "multi_capability"
    return {
        "task_type": primary,
        "required_capabilities": matches,
        "confidence": confidence,
        "matched_rules": matches,
        "needs_clarification": False,
        "automatic_execution": False,
    }

def recommend_capable_models(classification, registry: ModelCapabilityRegistry):
    if not isinstance(classification, dict) or not isinstance(registry, ModelCapabilityRegistry):
        raise TypeError("invalid_classification_or_registry")
    required = classification.get("required_capabilities")
    if not isinstance(required, list):
        raise ValueError("invalid_classification")
    candidates = registry.find(required, verified_only=True)
    return {
        "required_capabilities": list(required),
        "candidates": [item.to_dict() for item in candidates],
        "selection_policy": "all_required_capabilities_and_verified_runtime",
        "activation_policy": "manual_approval_required",
        "automatic_activation": False,
    }
