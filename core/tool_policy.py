"""Deterministic default-deny policy decisions for Yuki tool execution.

This module is a policy contract and evaluator, not an execution sandbox. Callers
must still route every side effect through it and enforce the returned decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Iterable


class Decision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"
    QUARANTINE = "quarantine"


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_ID = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._:/@+-]{0,255}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _bounded_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{field} must be a non-empty bounded identifier")
    return value


def argument_digest(arguments: object) -> str:
    """Hash canonical JSON arguments; reject non-JSON and non-finite numbers."""
    try:
        encoded = json.dumps(
            arguments, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError("arguments must be valid finite JSON data") from error
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PolicyRequest:
    principal_id: str
    trigger: str
    capability: str
    target: str
    argument_sha256: str
    risk: Risk
    run_id: str
    step_id: str
    claim_token_sha256: str | None = None

    def __post_init__(self) -> None:
        for field in ("principal_id", "trigger", "capability", "target", "run_id", "step_id"):
            _bounded_identifier(getattr(self, field), field)
        if not isinstance(self.argument_sha256, str) or not _DIGEST.fullmatch(self.argument_sha256):
            raise ValueError("argument_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.risk, Risk):
            raise ValueError("risk must be a Risk value")
        if self.claim_token_sha256 is not None and (
            not isinstance(self.claim_token_sha256, str) or not _DIGEST.fullmatch(self.claim_token_sha256)
        ):
            raise ValueError("claim_token_sha256 must be a lowercase SHA-256 digest")


@dataclass(frozen=True)
class PolicyRule:
    rule_id: str
    decision: Decision
    capability: str | None = None
    principal_id: str | None = None
    trigger: str | None = None
    target: str | None = None
    risk: Risk | None = None

    def __post_init__(self) -> None:
        _bounded_identifier(self.rule_id, "rule_id")
        if not isinstance(self.decision, Decision):
            raise ValueError("decision must be a Decision value")
        for field in ("capability", "principal_id", "trigger", "target"):
            value = getattr(self, field)
            if value is not None:
                _bounded_identifier(value, field)
        if self.risk is not None and not isinstance(self.risk, Risk):
            raise ValueError("risk must be a Risk value")

    def matches(self, request: PolicyRequest) -> bool:
        return all(
            expected is None or expected == getattr(request, field)
            for field, expected in (
                ("capability", self.capability),
                ("principal_id", self.principal_id),
                ("trigger", self.trigger),
                ("target", self.target),
                ("risk", self.risk),
            )
        )


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    policy_version: str
    matched_rule_ids: tuple[str, ...]
    reason: str


class ToolPolicyEngine:
    """Evaluate explicit rules with deny-overrides and fail-closed defaults."""

    _PRIORITY = {
        Decision.ALLOW: 0,
        Decision.REQUIRE_APPROVAL: 1,
        Decision.DENY: 2,
        Decision.QUARANTINE: 3,
    }

    def __init__(self, rules: Iterable[PolicyRule], *, policy_version: str):
        self.policy_version = _bounded_identifier(policy_version, "policy_version")
        if isinstance(rules, (str, bytes)):
            raise TypeError("rules must be an iterable of PolicyRule objects")
        materialized = tuple(rules)
        if any(not isinstance(rule, PolicyRule) for rule in materialized):
            raise TypeError("every policy rule must be a PolicyRule")
        ids = [rule.rule_id for rule in materialized]
        if len(ids) != len(set(ids)):
            raise ValueError("policy rule IDs must be unique")
        self._rules = materialized

    def evaluate(self, request: PolicyRequest) -> PolicyResult:
        if not isinstance(request, PolicyRequest):
            return PolicyResult(
                Decision.DENY, self.policy_version, (), "invalid_request",
            )
        matched = tuple(rule for rule in self._rules if rule.matches(request))
        if not matched:
            return PolicyResult(
                Decision.DENY, self.policy_version, (), "no_matching_rule",
            )
        highest = max(self._PRIORITY[rule.decision] for rule in matched)
        winning = tuple(rule for rule in matched if self._PRIORITY[rule.decision] == highest)
        decision = winning[0].decision
        return PolicyResult(
            decision, self.policy_version,
            tuple(rule.rule_id for rule in winning),
            "matched_policy_rules",
        )
