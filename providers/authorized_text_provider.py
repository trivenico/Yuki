"""Central authorization wrapper for text-only provider APIs."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from core.authorization import AuthorizationRequest, Principal
from core.authorization_gateway import AuthorizationGateway


class AuthorizedTextProvider:
    """Authorize one-shot generate/generate_for calls before provider dispatch."""

    def __init__(
        self,
        provider: Any,
        *,
        authorization_gateway: AuthorizationGateway,
        principal: Principal,
        provider_name: str,
        policy_revision: str,
    ):
        if provider is None or not callable(getattr(provider, "generate", None)):
            raise TypeError("provider must implement generate(prompt)")
        if not isinstance(authorization_gateway, AuthorizationGateway):
            raise TypeError("authorization_gateway must be an AuthorizationGateway")
        if not isinstance(principal, Principal):
            raise TypeError("principal must be a trusted Principal")
        for value, label in ((provider_name, "provider_name"), (policy_revision, "policy_revision")):
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(label + " must be non-empty bounded text")
        self.provider = provider
        self.authorization_gateway = authorization_gateway
        self.principal = principal
        self.provider_name = provider_name.strip()
        self.policy_revision = policy_revision.strip()

    def is_available(self):
        return self.provider.is_available() is True

    def _authorize(self, prompt: str, *, task: str | None = None):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 100_000:
            raise ValueError("prompt must be non-empty bounded text")
        cloud_tasks = getattr(self.provider, "cloud_tasks", frozenset())
        cloud_route = isinstance(task, str) and task.strip().lower() in cloud_tasks
        action = "inference:cloud:invoke" if cloud_route else "inference:invoke"
        envelope = {
            "provider": self.provider_name,
            "provider_type": type(self.provider).__module__ + "." + type(self.provider).__qualname__,
            "model": getattr(self.provider, "model", None),
            "task": task,
            "route": "cloud" if cloud_route else "default",
            "prompt": prompt,
        }
        digest = hashlib.sha256(json.dumps(
            envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("utf-8")).hexdigest()
        self.authorization_gateway.require(AuthorizationRequest(
            principal=self.principal,
            action=action,
            resource="inference:sha256:" + digest,
            risk="high",
            expected_policy_revision=self.policy_revision,
        ))

    def generate(self, prompt: str) -> str:
        self._authorize(prompt)
        return self.provider.generate(prompt)

    def generate_for(self, task: str, prompt: str) -> str:
        if not callable(getattr(self.provider, "generate_for", None)):
            raise RuntimeError("provider does not support task-specific routing")
        if not isinstance(task, str) or not task.strip() or len(task) > 128:
            raise ValueError("task must be non-empty bounded text")
        self._authorize(prompt, task=task)
        return self.provider.generate_for(task, prompt)

    def close(self):
        close = getattr(self.provider, "close", None)
        if callable(close):
            return close()
