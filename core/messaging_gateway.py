"""Minimal authenticated inbound webhook primitives; provider SDKs stay optional."""
from __future__ import annotations
import hashlib
import hmac
import json
import time
from typing import Any, Callable

class WebhookError(ValueError): pass

def verify_hmac_sha256(secret: str, body: bytes, signature: str, *, max_age_seconds: int | None = None,
                       timestamp: str | None = None, now: int | None = None) -> bool:
    if not isinstance(secret,str) or not secret or not isinstance(body,bytes) or len(body)>1_000_000: return False
    if not isinstance(signature,str): return False
    if max_age_seconds is not None:
        try: ts=int(timestamp or "")
        except ValueError: return False
        current=int(time.time()) if now is None else now
        if abs(current-ts)>max_age_seconds: return False
    expected=hmac.new(secret.encode(),body,hashlib.sha256).hexdigest()
    supplied=signature.removeprefix("sha256=")
    return hmac.compare_digest(expected,supplied)

class MessagingGateway:
    """Channel adapters normalize trusted provider events into a bounded task callback."""
    def __init__(self, handler: Callable[[str,str,str],Any], *, allowed_users: dict[str,set[str]],
                 max_text_chars: int=4000):
        if not callable(handler): raise TypeError("handler callback is required")
        self.handler, self.allowed_users, self.max_text_chars=handler, {k:set(v) for k,v in allowed_users.items()}, max_text_chars

    def dispatch(self, channel: str, user_id: str, text: str):
        if channel not in self.allowed_users or user_id not in self.allowed_users[channel]:
            raise PermissionError("Messaging user is not allowlisted")
        if not isinstance(text,str) or not text.strip() or len(text)>self.max_text_chars:
            raise WebhookError("Message is empty or exceeds configured length")
        return self.handler(channel,user_id,text)
