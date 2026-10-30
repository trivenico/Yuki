"""Guarded runtime facade for explicitly approved, registered skills.

Discovery/registration approval and invocation approval are separate controls.
This facade never imports entrypoints and routes every invocation through the
shared guarded skill runtime.
"""
from __future__ import annotations

from typing import Any, Callable
import hashlib
import threading

from core.approved_skill_wiring import ApprovedSkillWiringService, SkillApprovalStore
from core.guarded_skill_runtime import OneShotSkillApprovals, SkillInvocationResult, _callable_code_identity, invoke_registered_skill
from skills.metadata_discovery import SkillCandidate


class ApprovedSkillRuntime:
    def __init__(
        self,
        registry: Any,
        approval_store: SkillApprovalStore,
        invocation_approvals: OneShotSkillApprovals | None = None,
        audit: Callable[[str, str], None] | None = None,
    ):
        self.registry = registry
        self.approval_store = approval_store
        self.invocation_approvals = invocation_approvals or OneShotSkillApprovals()
        self.audit = audit
        self.wiring = ApprovedSkillWiringService(registry, approval_store)
        self._instance_lock = threading.Lock()
        self._approved_instances: dict[tuple[str, str], tuple[Any, Any, str, bool, Any]] = {}
        self._in_flight: set[tuple[str, str]] = set()

    @staticmethod
    def _approval_key(skill_name: str, argument: str) -> tuple[str, str]:
        if not isinstance(skill_name, str) or not skill_name or len(skill_name) > 128:
            raise ValueError("A non-empty bounded skill name is required.")
        if not isinstance(argument, str) or len(argument) > 20000:
            raise ValueError("Approval argument must be text no longer than 20000 characters.")
        try:
            encoded = argument.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("Approval argument must be valid UTF-8 text.") from exc
        return skill_name, hashlib.sha256(encoded).hexdigest()

    def discover(self, root):
        return self.wiring.discover(root)

    def wire_approved(self, candidates: tuple[SkillCandidate, ...], trusted_instances: dict[str, Any]) -> tuple[str, ...]:
        # Refuse an instance whose declared risk differs from reviewed metadata.
        eligible = []
        for candidate in candidates:
            instance = trusted_instances.get(candidate.name)
            if instance is None:
                continue
            instance_risk = getattr(instance, "risk", "high")
            if not isinstance(instance_risk, str) or instance_risk.lower() != candidate.risk:
                continue
            eligible.append(candidate)
        return self.wiring.wire_approved(tuple(eligible), trusted_instances)

    def approve_invocation(self, skill_name: str, argument: str = "") -> None:
        # Approval creation is a security boundary too: validate the exact
        # request before registry access so malformed requests cannot trigger
        # arbitrary registry behavior or create mismatched approval keys.
        if not isinstance(skill_name, str) or not skill_name or len(skill_name) > 128:
            raise ValueError("Invalid skill name.")
        if not isinstance(argument, str) or len(argument) > 20000:
            raise ValueError("Argument must be text no longer than 20000 characters.")
        try:
            argument.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Argument must be valid UTF-8 text.") from None
        key = self._approval_key(skill_name, argument)
        try:
            skill = self.registry.get(skill_name)
        except Exception:
            raise RuntimeError("Skill registry is unavailable; cannot approve invocation.") from None
        if skill is None:
            raise ValueError("Cannot approve an invocation for an unregistered skill.")
        # Capture the exact callable and authorization metadata, not just the
        # mutable skill object. The guarded dispatcher verifies this descriptor
        # and invokes the captured callable rather than resolving it again.
        execute = getattr(skill, "execute", None)
        if not callable(execute):
            execute = getattr(skill, "run", None)
        risk = str(getattr(skill, "risk", "high")).lower()
        requires_approval = bool(getattr(skill, "requires_approval", True))
        execution_target = execute if callable(execute) else None
        execution_code = _callable_code_identity(execution_target)
        descriptor = (skill, execution_target, risk, requires_approval, execution_code)
        # Store the descriptor and approval together so invocation cannot
        # observe a partially-created authorization.
        with self._instance_lock:
            # Do not mint or replace an approval while the same request is
            # already executing. Otherwise a concurrent approval could leave
            # a fresh token/snapshot behind after the in-flight call finishes.
            if key in self._in_flight:
                raise RuntimeError("An invocation for this approved request is already in progress.")
            self.invocation_approvals.approve(skill_name, argument)
            self._approved_instances[key] = descriptor

    def invoke(self, skill_name: str, argument: str = "") -> SkillInvocationResult:
        # Validate before touching the registry. The guarded runtime performs
        # the same checks, but this facade must inspect risk before dispatch.
        if not isinstance(skill_name, str) or not skill_name or len(skill_name) > 128:
            if self.audit is not None:
                try:
                    self.audit("skill:invoke", "invalid_name")
                except Exception:
                    pass
            return SkillInvocationResult(False, "Invalid skill name.", str(skill_name)[:128], "invalid_name")
        if not isinstance(argument, str) or len(argument) > 20000:
            if self.audit is not None:
                try:
                    self.audit("skill:invoke", "invalid_argument")
                except Exception:
                    pass
            return SkillInvocationResult(False, "Argument must be text no longer than 20000 characters.", skill_name, "invalid_argument")
        try:
            argument.encode("utf-8")
        except UnicodeEncodeError:
            if self.audit is not None:
                try:
                    self.audit("skill:invoke", "invalid_argument")
                except Exception:
                    pass
            return SkillInvocationResult(False, "Argument must be valid UTF-8 text.", skill_name, "invalid_argument")
        try:
            skill = self.registry.get(skill_name)
        except Exception:
            # A transient registry failure must not strand a facade-bound
            # approval or its instance snapshot. Never disturb an invocation
            # that another thread has already claimed.
            try:
                failed_key = self._approval_key(skill_name, argument)
            except ValueError:
                failed_key = None
            if failed_key is not None:
                with self._instance_lock:
                    if failed_key not in self._in_flight:
                        failed_snapshot = self._approved_instances.pop(failed_key, None)
                        if failed_snapshot is not None:
                            self.invocation_approvals.consume(skill_name, argument)
            if self.audit is not None:
                try:
                    self.audit("skill:invoke", "registry_failed")
                except Exception:
                    pass
            return SkillInvocationResult(False, "Skill registry is unavailable.", skill_name, "registry_failed")
        try:
            key = self._approval_key(skill_name, argument)
        except ValueError:
            key = None
        with self._instance_lock:
            if key is not None and key in self._in_flight:
                return SkillInvocationResult(
                    False, "An invocation for this approved request is already in progress.",
                    skill_name, "approval_in_use"
                )
            approved_instance = self._approved_instances.pop(key, None) if key is not None else None
            if key is not None and approved_instance is not None:
                self._in_flight.add(key)

        # If the approved skill disappeared, or the current instance is
        # low-risk and does not need an invocation approval, release the
        # one-shot token now. Otherwise it would remain pending after its
        # facade snapshot has been consumed, eventually exhausting the cap.
        if approved_instance is not None and (
            skill is None
            or (
                not approved_instance[3]
                and approved_instance[2] in {"low", "medium"}
            )
        ):
            self.invocation_approvals.consume(skill_name, argument)

        # Without a facade-created instance snapshot, preserve the public
        # approval_required status and never let a separately inserted approval
        # bypass the instance-binding boundary.
        if approved_instance is None and skill is not None:
            risk = str(getattr(skill, "risk", "high")).lower()
            needs_approval = (
                bool(getattr(skill, "requires_approval", True))
                or risk not in {"low", "medium", "high", "critical"}
                or risk in {"high", "critical"}
            )
            if needs_approval:
                # If a token exists without its facade-bound instance snapshot,
                # burn it and reject it as an identity-binding violation. With
                # no token, retain the normal approval_required response.
                if self.invocation_approvals.consume(skill_name, argument):
                    status = "skill_identity_changed"
                    message = "Approval was not bound to the current skill instance."
                else:
                    status = "approval_required"
                    message = "Skill invocation requires facade-bound approval."
                if self.audit is not None:
                    try:
                        self.audit("skill:invoke", status)
                    except Exception:
                        # Audit sink failures must not alter authorization behavior.
                        pass
                return SkillInvocationResult(False, message, skill_name, status)

        expected_skill = approved_instance[0] if approved_instance is not None else skill
        try:
            return invoke_registered_skill(
                self.registry,
                skill_name,
                argument,
                approvals=self.invocation_approvals,
                audit=self.audit,
                expected_skill=expected_skill,
                expected_execution=approved_instance,
            )
        finally:
            if key is not None and approved_instance is not None:
                with self._instance_lock:
                    self._in_flight.discard(key)
