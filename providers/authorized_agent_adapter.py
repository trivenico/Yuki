"""Central authorization at the direct model-provider request boundary."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from core.authorization import AuthorizationRequest, Principal
from core.authorization_gateway import AuthorizationGateway
from providers.agent_adapter import ProviderAgentAdapter


class AuthorizedProviderAgentAdapter(ProviderAgentAdapter):
    """Authorize every non-streaming and streaming provider invocation.

    Identity and task/delegation/approval context are injected by trusted host
    composition. They are never accepted from prompts, tool arguments, or model
    output. The audit resource is a digest, not raw prompt or tool content.
    """

    def __init__(
        self,
        provider: Any,
        *,
        authorization_gateway: AuthorizationGateway,
        principal: Principal,
        provider_name: str | None = None,
        model_id: str | None = None,
        task_id: str | None = None,
        delegation_id: str | None = None,
        approval_id: str | None = None,
        policy_revision: str | None = None,
    ):
        super().__init__(provider)
        if not isinstance(authorization_gateway, AuthorizationGateway):
            raise TypeError("authorization_gateway must be an AuthorizationGateway")
        if not isinstance(principal, Principal):
            raise TypeError("principal must be a trusted Principal")
        provider_name = provider_name or type(provider).__name__.lower()
        model_id = model_id or getattr(provider, "model", None) or "unspecified"
        for value, label in ((provider_name, "provider_name"), (model_id, "model_id")):
            if not isinstance(value, str) or not value.strip() or len(value) > 120:
                raise ValueError(label + " must be non-empty text of at most 120 characters")
        self.authorization_gateway = authorization_gateway
        self.principal = principal
        self.provider_name = provider_name.strip()
        self.model_id = model_id.strip()
        self.task_id = task_id
        self.delegation_id = delegation_id
        self.approval_id = approval_id
        self.policy_revision = policy_revision

    def set_provider(self, provider: Any, *, provider_name: str | None = None) -> None:
        """Replace the selected provider without dropping the authorization wrapper."""
        if provider is None or not callable(getattr(provider, "generate_chat", None)):
            raise TypeError("provider must implement generate_chat")
        name = provider_name or type(provider).__name__.lower()
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("provider_name must be non-empty text of at most 120 characters")
        model = getattr(provider, "model", None) or "unspecified"
        if not isinstance(model, str) or not model.strip() or len(model) > 120:
            raise ValueError("provider model identity is invalid")
        self.provider = provider
        self.provider_name = name.strip()
        self.model_id = model.strip()

    def _authorize_invocation(self, messages, tools, tool_choice):
        current_model = getattr(self.provider, "model", self.model_id)
        if current_model != self.model_id:
            raise RuntimeError("provider_model_identity_changed")
        endpoint = getattr(self.provider, "base_url", None)
        if endpoint is not None and (
            not isinstance(endpoint, str) or not endpoint.strip() or len(endpoint) > 2048
        ):
            raise RuntimeError("provider_endpoint_identity_invalid")
        envelope = {
            "provider": self.provider_name,
            "provider_type": type(self.provider).__module__ + "." + type(self.provider).__qualname__,
            "model": self.model_id,
            "endpoint": endpoint,
            "messages": list(messages),
            "tools": list(tools),
            "tool_choice": tool_choice,
        }
        try:
            canonical = json.dumps(
                envelope, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True, allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("provider invocation is not canonicalizable") from exc
        fingerprint = hashlib.sha256(canonical).hexdigest()
        request = AuthorizationRequest(
            principal=self.principal,
            action="inference:invoke",
            resource="inference:sha256:" + fingerprint,
            risk="high",
            task_id=self.task_id,
            delegation_id=self.delegation_id,
            approval_id=self.approval_id,
            expected_policy_revision=self.policy_revision,
        )
        self.authorization_gateway.require(request)

    def __call__(
        self, *, messages: Sequence[Mapping[str, str]],
        tools: Sequence[dict[str, Any]], tool_choice: str = "auto",
    ) -> Mapping[str, Any]:
        self._validate_inputs(messages, tools, tool_choice)
        self._authorize_invocation(messages, tools, tool_choice)
        return super().__call__(messages=messages, tools=tools, tool_choice=tool_choice)

    def stream_model(
        self, *, messages: Sequence[Mapping[str, str]],
        tools: Sequence[dict[str, Any]], tool_choice: str = "auto",
    ):
        self._validate_inputs(messages, tools, tool_choice)
        self._authorize_invocation(messages, tools, tool_choice)
        return super().stream_model(messages=messages, tools=tools, tool_choice=tool_choice)
