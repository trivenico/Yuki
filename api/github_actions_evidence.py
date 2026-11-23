"""ASGI ingress for authenticated GitHub Actions evidence webhooks."""
from __future__ import annotations

import asyncio
import json

from integrations.github_actions_evidence import GitHubActionsEvidenceWebhook, GitHubWebhookError

_WEBHOOK_PATH = "/webhooks/github/actions"
_MAX_BODY = 1_000_000


class GitHubActionsEvidenceAPI:
    """Expose one HMAC-authenticated webhook route without bearer-session auth."""

    def __init__(self, app, webhook: GitHubActionsEvidenceWebhook):
        if not isinstance(webhook, GitHubActionsEvidenceWebhook):
            raise TypeError("webhook must be a GitHubActionsEvidenceWebhook.")
        self.app = app
        self.webhook = webhook

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != _WEBHOOK_PATH:
            await self.app(scope, receive, send)
            return
        if scope.get("method", "GET").upper() != "POST":
            await self._respond(send, 405, {"error": "method_not_allowed"})
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        signature = headers.get(b"x-hub-signature-256", b"").decode("ascii", "ignore")
        event = headers.get(b"x-github-event", b"").decode("ascii", "ignore")
        delivery = headers.get(b"x-github-delivery", b"").decode("ascii", "ignore")
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > _MAX_BODY:
                await self._respond(send, 413, {"error": "payload_too_large"})
                return
            if not message.get("more_body", False):
                break
        try:
            result = await asyncio.to_thread(
                self.webhook.handle,
                raw_body=bytes(body),
                signature_256=signature,
                event_name=event,
                delivery_id=delivery,
            )
        except GitHubWebhookError:
            await self._respond(send, 401, {"error": "webhook_rejected"})
            return
        except Exception:
            await self._respond(send, 503, {"error": "webhook_processing_failed"})
            return
        await self._respond(send, 202 if result.accepted else 200, {
            "accepted": result.accepted,
            "evidence_id": result.evidence_id,
            "task_id": result.task_id,
            "task_version": result.task_version,
            "reason": result.reason,
        })

    @staticmethod
    async def _respond(send, status: int, payload: dict):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
