"""Build a fresh, owner-bound agent loop from persistent provider profiles.

The factory never falls back to process-global provider credentials. Every call
resolves the authenticated owner's selected profile from the profile store and
OS credential vault, then creates a new model adapter and execution loop.
"""
from __future__ import annotations

import threading
import weakref
from typing import Any, Callable, Mapping

from core.agent_execution_loop import AgentExecutionLoop
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol
from providers.profile_store import ProviderProfileStore
from yuki_providers import ProviderManager


class _DenyAllPolicy:
    """Safe default when the host has not supplied an owner tool runtime."""

    def authorize_tool(self, name: str) -> bool:
        return False


class OwnerAgentLoopFactory:
    """Resolve one authenticated owner's selected provider into an isolated loop.

    tool_runtime_for_owner must return a fresh GuardedToolRuntime whose
    handlers, policy and audit callback are bound to the supplied owner. The
    factory rejects a runtime or policy object previously assigned to another
    owner. If no callback is supplied, tools are denied for every owner.
    """

    def __init__(
        self,
        profile_store: ProviderProfileStore,
        *,
        tool_runtime_for_owner: Callable[[str], GuardedToolRuntime] | None = None,
        loop_options: Mapping[str, Any] | None = None,
    ) -> None:
        required = ("list_profiles", "selected_profile", "resolve_for_runtime")
        if not all(callable(getattr(profile_store, name, None)) for name in required):
            raise TypeError("profile_store does not implement the owner profile runtime contract")
        if tool_runtime_for_owner is not None and not callable(tool_runtime_for_owner):
            raise TypeError("tool_runtime_for_owner must be callable")
        if loop_options is not None and not isinstance(loop_options, Mapping):
            raise TypeError("loop_options must be a mapping")
        options = dict(loop_options or {})
        if {"model", "tool_protocol"} & set(options):
            raise ValueError("loop_options cannot override the model or tool protocol")
        self._profile_store = profile_store
        self._tool_runtime_for_owner = tool_runtime_for_owner
        self._loop_options = options
        self._runtime_owners = weakref.WeakKeyDictionary()
        self._policy_owners = weakref.WeakKeyDictionary()
        self._audit_owners = weakref.WeakKeyDictionary()
        self._lock = threading.RLock()

    @property
    def profile_store(self) -> ProviderProfileStore:
        """Expose the exact store used for resolution to compose API lifecycle safely."""
        return self._profile_store

    def __call__(self, owner_id: str) -> AgentExecutionLoop:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 256:
            raise ValueError("an authenticated owner identity is required")

        # A manager is deliberately created per call. It has no process
        # environment credentials and is populated only from this owner's rows.
        manager = ProviderManager({"provider": "openai"}, environ={})
        loaded = manager.load_persisted_profiles(self._profile_store, owner_id)
        selected = self._profile_store.selected_profile(owner_id)
        if not selected or selected not in loaded:
            raise RuntimeError("authenticated owner has no available selected provider profile")
        selection = manager.select(selected)
        if not selection.startswith("Selected provider:"):
            raise RuntimeError("authenticated owner's selected provider could not be constructed")
        model = manager.create_selected_agent_model()

        if self._tool_runtime_for_owner is None:
            tool_runtime = GuardedToolRuntime({}, _DenyAllPolicy())
        else:
            tool_runtime = self._tool_runtime_for_owner(owner_id)
        if not isinstance(tool_runtime, GuardedToolRuntime):
            raise TypeError("owner tool runtime factory must return GuardedToolRuntime")

        policy = tool_runtime.authorization_policy
        audit = getattr(tool_runtime, "audit_callback", None)
        # Bound-method objects are recreated on attribute access; key them by
        # their owning sink instance so two owners cannot share one sink object.
        audit_identity = getattr(audit, "__self__", None) or audit
        if policy is None or not callable(getattr(policy, "authorize_tool", None)):
            raise TypeError("owner tool runtime must contain an authorization policy")

        # Weak identity registries avoid retaining old runtimes while preventing
        # a mutable runtime or policy from crossing authenticated owner scopes.
        with self._lock:
            try:
                previous_owner = self._runtime_owners.get(tool_runtime)
                previous_policy_owner = self._policy_owners.get(policy)
                previous_audit_owner = self._audit_owners.get(audit_identity) if audit is not None else None
            except TypeError as exc:
                raise TypeError("owner tool runtime and policy must support weak identity tracking") from exc
            if previous_owner is not None and previous_owner != owner_id:
                raise PermissionError("a guarded tool runtime cannot be shared across owners")
            if previous_policy_owner is not None and previous_policy_owner != owner_id:
                raise PermissionError("a tool authorization policy cannot be shared across owners")
            if previous_audit_owner is not None and previous_audit_owner != owner_id:
                raise PermissionError("a tool audit sink cannot be shared across owners")
            self._runtime_owners[tool_runtime] = owner_id
            self._policy_owners[policy] = owner_id
            if audit is not None:
                try:
                    self._audit_owners[audit_identity] = owner_id
                except TypeError as exc:
                    raise TypeError("owner audit sinks must support weak identity tracking") from exc

        return AgentExecutionLoop(
            model,
            StructuredToolProtocol(tool_runtime),
            **self._loop_options,
        )
