"""Immutable, content-addressed benchmark definitions.

A registered benchmark revision is an evaluation input, not a trust or promotion
decision. Registry state is process-local; durable storage is a separate adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from threading import RLock
from typing import Iterable

from evaluation.model_eval import EvalCase

_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$")


@dataclass(frozen=True, slots=True)
class BenchmarkDefinition:
    benchmark_id: str
    version: int
    name: str
    cases: tuple[EvalCase, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.benchmark_id, str) or not _ID.fullmatch(self.benchmark_id):
            raise ValueError("benchmark_id must be a bounded identifier")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("version must be a positive integer")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 160:
            raise ValueError("name must be non-empty bounded text")
        if not isinstance(self.description, str) or len(self.description) > 1000:
            raise ValueError("description must be bounded text")
        if not isinstance(self.cases, tuple) or not 1 <= len(self.cases) <= 200:
            raise ValueError("cases must be a tuple containing 1..200 cases")
        if any(not isinstance(case, EvalCase) for case in self.cases):
            raise TypeError("cases must contain EvalCase instances")
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("benchmark case IDs must be unique")
        # Reuse evaluator validation so benchmark definitions obey exactly the
        # same constraints as runtime evaluation.
        for case in self.cases:
            ModelCaseValidator.validate(case)

    def canonical_payload(self) -> str:
        payload = {
            "benchmark_id": self.benchmark_id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "cases": [
                {
                    "case_id": case.case_id,
                    "task": case.task,
                    "prompt": case.prompt,
                    "required_terms": list(case.required_terms),
                    "forbidden_terms": list(case.forbidden_terms),
                    "max_latency_seconds": case.max_latency_seconds,
                    "max_response_chars": case.max_response_chars,
                }
                for case in self.cases
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_payload().encode("utf-8")).hexdigest()


class ModelCaseValidator:
    @staticmethod
    def validate(case: EvalCase) -> None:
        # Keep benchmark validation aligned with the evaluator's public contract.
        from evaluation.model_eval import ModelEvaluator
        ModelEvaluator._validate_case(case)
        if len(case.prompt) > 8_000:
            raise ValueError("benchmark prompt exceeds the default 8000-character limit")


class BenchmarkRegistry:
    """Thread-safe registry keyed by benchmark ID and monotonically versioned revision."""

    def __init__(self, *, max_revisions: int = 500) -> None:
        if type(max_revisions) is not int or not 1 <= max_revisions <= 10_000:
            raise ValueError("max_revisions must be an integer between 1 and 10000")
        self._max_revisions = max_revisions
        self._definitions: dict[tuple[str, int], BenchmarkDefinition] = {}
        self._lock = RLock()

    def register(self, definition: BenchmarkDefinition) -> BenchmarkDefinition:
        if not isinstance(definition, BenchmarkDefinition):
            raise TypeError("definition must be a BenchmarkDefinition")
        key = (definition.benchmark_id, definition.version)
        with self._lock:
            existing = self._definitions.get(key)
            if existing is not None:
                if existing.digest != definition.digest:
                    raise ValueError("benchmark revisions are immutable; increment version for changes")
                return existing
            versions = [
                version for benchmark_id, version in self._definitions
                if benchmark_id == definition.benchmark_id
            ]
            if versions and definition.version != max(versions) + 1:
                raise ValueError("new benchmark revisions must increment the latest version by one")
            if not versions and definition.version != 1:
                raise ValueError("first benchmark revision must be version 1")
            if len(self._definitions) >= self._max_revisions:
                raise ValueError("benchmark registry capacity reached")
            self._definitions[key] = definition
            return definition

    def resolve(self, benchmark_id: str, version: int, *, expected_digest: str | None = None) -> BenchmarkDefinition:
        if not isinstance(benchmark_id, str) or type(version) is not int:
            raise ValueError("benchmark ID and integer version are required")
        with self._lock:
            definition = self._definitions.get((benchmark_id, version))
            if definition is None:
                raise KeyError("benchmark revision not found")
            if expected_digest is not None:
                if not isinstance(expected_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_digest):
                    raise ValueError("expected_digest must be a SHA-256 hex digest")
                if definition.digest != expected_digest:
                    raise ValueError("benchmark digest does not match requested revision")
            return definition

    def list_revisions(self, benchmark_id: str) -> tuple[tuple[int, str], ...]:
        if not isinstance(benchmark_id, str) or not _ID.fullmatch(benchmark_id):
            raise ValueError("benchmark_id must be a bounded identifier")
        with self._lock:
            return tuple(
                (version, self._definitions[(benchmark_id, version)].digest)
                for version in sorted(v for key, v in self._definitions if key == benchmark_id)
            )
