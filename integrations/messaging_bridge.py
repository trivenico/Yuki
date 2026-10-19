"""Webhook command bridge for Discord and Telegram.

No server is started implicitly. The hosting application must provide HTTPS, request
size limits, rate limiting, and pass the raw request bytes to these verification methods.
Only a small command allow-list is dispatched; all non-read-only commands need approval.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import time
from collections import defaultdict, deque
from typing import Any, Callable

MAX_BODY = 64_000
MAX_AGE_SECONDS = 300
READ_ONLY = frozenset({"help", "status", "skills", "reminders"})
COMMANDS = frozenset({"help", "status", "skills", "reminders", "ask", "task", "cancel", "approve", "deny"})

class WebhookError(ValueError):
    pass

class SlidingWindowRateLimit:
    def __init__(self, *, limit: int = 20, window_seconds: int = 60):
        if type(limit) is not int or not 1 <= limit <= 1000 or type(window_seconds) is not int or not 1 <= window_seconds <= 3600:
            raise ValueError("Invalid rate limit.")
        self.limit, self.window_seconds = limit, window_seconds
        self._events = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        events = self._events[key]
        while events and events[0] <= now - self.window_seconds:
            events.popleft()
        if len(events) >= self.limit:
            return False
        events.append(now)
        return True

class MessagingCommandBridge:
    def __init__(self, *, dispatch: Callable[[str, str, dict[str, Any]], str],
                 approve_action: Callable[[str, str], bool],
                 allowed_user_ids: set[str] | frozenset[str],
                 rate_limiter: SlidingWindowRateLimit | None = None):
        if not callable(dispatch) or not callable(approve_action):
            raise ValueError("Dispatcher and approval callback are mandatory.")
        if not allowed_user_ids or any(not isinstance(v, str) or not v.strip() for v in allowed_user_ids):
            raise ValueError("An explicit user ID allow-list is required.")
        self.dispatch, self.approve_action = dispatch, approve_action
        self.allowed_user_ids = frozenset(allowed_user_ids)
        self.rate_limiter = rate_limiter or SlidingWindowRateLimit()

    @staticmethod
    def _body(raw: bytes) -> dict[str, Any]:
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BODY:
            raise WebhookError("Webhook body is empty or exceeds limits.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise WebhookError("Webhook body must be UTF-8 JSON.") from None
        if not isinstance(payload, dict):
            raise WebhookError("Webhook payload must be a JSON object.")
        return payload

    def _dispatch(self, platform: str, user_id: str, command_text: str) -> str:
        if user_id not in self.allowed_user_ids:
            raise PermissionError("Sender is not authorized.")
        if not self.rate_limiter.allow(platform + ":" + user_id):
            raise WebhookError("Rate limit exceeded.")
        if not isinstance(command_text, str) or len(command_text) > 2000:
            raise WebhookError("Command must be bounded text.")
        parts = command_text.strip().split(maxsplit=1)
        if not parts:
            return "Send /help for available commands."
        command = parts[0].lstrip("/").lower()
        argument = parts[1] if len(parts) > 1 else ""
        if command not in COMMANDS:
            return "Command not available through messaging."
        if command not in READ_ONLY and self.approve_action(platform, command_text) is not True:
            return "Action not approved; nothing was executed."
        result = self.dispatch(command, argument, {"platform": platform, "user_id": user_id})
        return result[:4000] if isinstance(result, str) else "Command returned an unsupported response."

    def telegram(self, raw_body: bytes, *, secret_header: str | None, expected_secret: str,
                 allowed_user_ids: set[str] | frozenset[str] | None = None) -> str:
        if not expected_secret or not isinstance(secret_header, str) or not hmac.compare_digest(secret_header, expected_secret):
            raise PermissionError("Telegram webhook secret verification failed.")
        payload = self._body(raw_body)
        message = payload.get("message") or payload.get("edited_message")
        if not isinstance(message, dict) or not isinstance(message.get("from"), dict) or not isinstance(message.get("text"), str):
            raise WebhookError("Unsupported Telegram update.")
        user_id = str(message["from"].get("id", ""))
        return self._dispatch("telegram", user_id, message["text"])

    def discord(self, raw_body: bytes, *, signature: str, timestamp: str, public_key_hex: str) -> dict[str, Any]:
        if not isinstance(timestamp, str) or not timestamp.isdigit() or abs(time.time() - int(timestamp)) > MAX_AGE_SECONDS:
            raise PermissionError("Discord timestamp is invalid or stale.")
        if not isinstance(signature, str) or len(signature) != 128:
            raise PermissionError("Discord signature is invalid.")
        try:
            from nacl.signing import VerifyKey
            VerifyKey(bytes.fromhex(public_key_hex)).verify(timestamp.encode("ascii") + raw_body, bytes.fromhex(signature))
        except ImportError as exc:
            raise RuntimeError("Discord verification requires the optional PyNaCl package.") from exc
        except Exception as exc:
            raise PermissionError("Discord signature verification failed.") from exc
        payload = self._body(raw_body)
        # Discord interactions need an immediate PING response and interaction response envelope.
        if payload.get("type") == 1:
            return {"type": 1}
        data = payload.get("data")
        member = payload.get("member")
        user = member.get("user", {}) if isinstance(member, dict) else payload.get("user", {})
        user_id = str(user.get("id", "")) if isinstance(user, dict) else ""
        name = str(data.get("name", "")) if isinstance(data, dict) else ""
        options = data.get("options", []) if isinstance(data, dict) else []
        if name not in COMMANDS or not isinstance(options, list):
            return {"type": 4, "data": {"content": "Command not available through messaging.", "flags": 64}}
        argument = " ".join(str(item.get("value", ""))[:500] for item in options if isinstance(item, dict))
        text = "/" + name + (" " + argument if argument else "")
        try:
            response = self._dispatch("discord", user_id, text)
        except (PermissionError, WebhookError) as exc:
            response = str(exc)
        return {"type": 4, "data": {"content": response[:1900], "flags": 64}}
