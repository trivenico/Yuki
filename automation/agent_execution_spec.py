"""Versioned declarative agent execution specifications.

Specs describe requested execution constraints; they never grant permissions,
select providers, execute code, or bypass the task runtime's authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")
_MAX_SPEC_BYTES = 16 * 1024
_MAX_DEPENDENCIES = 32
_MAX_CAPABILITIES = 32
_MAX_INSTRUCTIONS = 4_000
_MAX_SCHEMA_BYTES = 4 * 1024
_ALLOWED_OUTPUTS = frozenset({"text", "json", "markdown", "file_reference"})
_ALLOWED_RETRY_POLICIES = frozenset({"manual_only", "idempotent_only"})
_ALLOWED_APPROVAL_POLICIES = frozenset({"required", "per_action", "not_requested"})


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{field} must be a bounded identifier.")
    return value


def _json_object(value: Any, field: str, limit: int) -> dict[str, Any]:
    if not isinstance(value, Mapping) or len(value) > 32:
        raise ValueError(f"{field} must be an object with at most 32 fields.")
    result = dict(value)
    try:
        encoded = json.dumps(
            result, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain finite JSON-compatible values.") from exc
    if len(encoded.encode("utf-8")) > limit:
        raise ValueError(f"{field} exceeds its {limit}-byte limit.")
    return result


@dataclass(frozen=True)
class ExecutionBudget:
    """Hard upper bounds requested by a spec; runtime enforcement is separate."""

    max_runtime_seconds: int = 300
    max_memory_mb: int = 512
    max_tokens: int = 8_000
    max_steps: int = 20

    def __post_init__(self) -> None:
        limits = {
            "max_runtime_seconds": (1, 86_400),
            "max_memory_mb": (16, 65_536),
            "max_tokens": (1, 1_000_000),
            "max_steps": (1, 20),
        }
        for field, (minimum, maximum) in limits.items():
            value = getattr(self, field)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{field} must be an integer from {minimum} to {maximum}.")


@dataclass(frozen=True)
class AgentExecutionSpec:
    """Immutable, versioned contract for one bounded agent role."""

    spec_id: str
    role: str
    instructions: str
    capabilities: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    input_contract: Mapping[str, Any] | None = None
    output_types: tuple[str, ...] = ("text",)
    approval_policy: str = "required"
    retry_policy: str = "manual_only"
    budget: ExecutionBudget = ExecutionBudget()
    version: int = 1

    def __post_init__(self) -> None:
        _identifier(self.spec_id, "spec_id")
        _identifier(self.role, "role")
        if not isinstance(self.instructions, str) or not self.instructions.strip() or len(self.instructions) > _MAX_INSTRUCTIONS or "\x00" in self.instructions:
            raise ValueError("instructions must be non-empty bounded text.")
        if type(self.version) is not int or self.version != 1:
            raise ValueError("unsupported execution spec version.")
        if not isinstance(self.budget, ExecutionBudget):
            raise TypeError("budget must be an ExecutionBudget.")
        for field, values, maximum in (
            ("capabilities", self.capabilities, _MAX_CAPABILITIES),
            ("dependencies", self.dependencies, _MAX_DEPENDENCIES),
            ("output_types", self.output_types, len(_ALLOWED_OUTPUTS)),
        ):
            if not isinstance(values, tuple) or len(values) > maximum:
                raise ValueError(f"{field} must be a bounded tuple.")
            if len(set(values)) != len(values):
                raise ValueError(f"{field} must not contain duplicates.")
        if any(not isinstance(item, str) or not _CAPABILITY.fullmatch(item) for item in self.capabilities):
            raise ValueError("capabilities must be bounded lowercase capability identifiers.")
        if any(not isinstance(item, str) or not _ID.fullmatch(item) for item in self.dependencies):
            raise ValueError("dependencies must be bounded spec identifiers.")
        if self.spec_id in self.dependencies:
            raise ValueError("a spec cannot depend on itself.")
        if not self.output_types or any(item not in _ALLOWED_OUTPUTS for item in self.output_types):
            raise ValueError("output_types must use the supported output contract types.")
        if not isinstance(self.approval_policy, str) or self.approval_policy not in _ALLOWED_APPROVAL_POLICIES:
            raise ValueError("unsupported approval_policy.")
        if not isinstance(self.retry_policy, str) or self.retry_policy not in _ALLOWED_RETRY_POLICIES:
            raise ValueError("unsupported retry_policy.")
        if self.input_contract is not None:
            _json_object(self.input_contract, "input_contract", _MAX_SCHEMA_BYTES)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible canonical representation."""
        value = {
            "version": self.version,
            "spec_id": self.spec_id,
            "role": self.role,
            "instructions": self.instructions,
            "capabilities": list(self.capabilities),
            "dependencies": list(self.dependencies),
            "input_contract": dict(self.input_contract) if self.input_contract is not None else None,
            "output_types": list(self.output_types),
            "approval_policy": self.approval_policy,
            "retry_policy": self.retry_policy,
            "budget": {
                "max_runtime_seconds": self.budget.max_runtime_seconds,
                "max_memory_mb": self.budget.max_memory_mb,
                "max_tokens": self.budget.max_tokens,
                "max_steps": self.budget.max_steps,
            },
        }
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > _MAX_SPEC_BYTES:
            raise ValueError("execution spec exceeds the 16384-byte limit.")
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentExecutionSpec":
        if not isinstance(value, Mapping):
            raise TypeError("execution spec must be an object.")
        allowed = {
            "version", "spec_id", "role", "instructions", "capabilities",
            "dependencies", "input_contract", "output_types", "approval_policy",
            "retry_policy", "budget",
        }
        if set(value) - allowed:
            raise ValueError("execution spec contains unsupported fields.")
        def tuple_field(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
            raw = value.get(name, default)
            if not isinstance(raw, (list, tuple)) or any(not isinstance(item, str) for item in raw):
                raise ValueError(f"{name} must be an array of strings.")
            return tuple(raw)

        budget_data = value.get("budget", {})
        if not isinstance(budget_data, Mapping) or set(budget_data) - {
            "max_runtime_seconds", "max_memory_mb", "max_tokens", "max_steps",
        }:
            raise ValueError("budget contains unsupported fields.")
        spec = cls(
            spec_id=value.get("spec_id"),
            role=value.get("role"),
            instructions=value.get("instructions"),
            capabilities=tuple_field("capabilities", ()),
            dependencies=tuple_field("dependencies", ()),
            input_contract=value.get("input_contract"),
            output_types=tuple_field("output_types", ("text",)),
            approval_policy=value.get("approval_policy", "required"),
            retry_policy=value.get("retry_policy", "manual_only"),
            budget=ExecutionBudget(**dict(budget_data)),
            version=value.get("version", 1),
        )
        spec.to_dict()
        return spec


def validate_execution_graph(specs: tuple[AgentExecutionSpec, ...]) -> tuple[str, ...]:
    """Validate dependency references and cycles; return a deterministic topological order."""
    if not isinstance(specs, tuple) or not 1 <= len(specs) <= 64:
        raise ValueError("specs must be a tuple containing 1-64 execution specs.")
    by_id = {}
    for spec in specs:
        if not isinstance(spec, AgentExecutionSpec):
            raise TypeError("every graph entry must be an AgentExecutionSpec.")
        if spec.spec_id in by_id:
            raise ValueError("execution spec IDs must be unique.")
        by_id[spec.spec_id] = spec
    for spec in specs:
        missing = set(spec.dependencies) - set(by_id)
        if missing:
            raise ValueError(f"unknown dependencies for {spec.spec_id}: {sorted(missing)}.")
    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[str] = []

    def visit(spec_id: str) -> None:
        if spec_id in visiting:
            raise ValueError("execution dependency graph contains a cycle.")
        if spec_id in visited:
            return
        visiting.add(spec_id)
        for dependency in sorted(by_id[spec_id].dependencies):
            visit(dependency)
        visiting.remove(spec_id)
        visited.add(spec_id)
        ordered.append(spec_id)

    for spec_id in sorted(by_id):
        visit(spec_id)
    return tuple(ordered)
