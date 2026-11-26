"""Strict, bounded message types for a future isolated browser worker.

This module validates serialization only. It does not authenticate IPC peers,
authorize a task, prevent replay, enforce network policy, or sandbox Chromium.
Those controls belong to the trusted launcher and deployment boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import re
from urllib.parse import urlsplit

MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 20_000
MAX_TASK_ID = 128
MAX_TEXT_CHARS = 12_000
_MIN_TIMEOUT_MS = 1_000
_MAX_TIMEOUT_MS = 30_000
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_OUTCOMES = frozenset({"ok", "blocked", "timeout", "error"})


class BrowserWorkerProtocolError(ValueError):
    """A browser-worker message is malformed or exceeds protocol bounds."""


def _bounded_text(value: object, field: str, maximum: int, *, allow_empty: bool = True) -> str:
    if type(value) is not str or len(value) > maximum or (not allow_empty and not value):
        raise BrowserWorkerProtocolError(f"{field} is invalid or exceeds its limit")
    if any(ord(char) < 32 and char not in "\\t\\n\\r" for char in value):
        raise BrowserWorkerProtocolError(f"{field} contains control characters")
    return value


def _validate_public_https_url(value: object, field: str = "url") -> str:
    url = _bounded_text(value, field, 2048, allow_empty=False)
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise BrowserWorkerProtocolError(f"{field} is malformed") from exc
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
    ):
        raise BrowserWorkerProtocolError(f"{field} must be HTTPS on port 443 without credentials")
    host = parts.hostname
    if host.endswith("."):
        raise BrowserWorkerProtocolError(f"{field} hostname must be canonical")
    try:
        host.encode("ascii", errors="strict")
        ipaddress.ip_address(host)
    except UnicodeEncodeError as exc:
        raise BrowserWorkerProtocolError(f"{field} hostname must use ASCII/IDNA form") from exc
    except ValueError:
        pass
    else:
        raise BrowserWorkerProtocolError(f"{field} must use a DNS hostname, not an IP literal")
    if any(not label or len(label) > 63 for label in host.split(".")):
        raise BrowserWorkerProtocolError(f"{field} hostname is malformed")
    return url


def _strict_object(raw: object, required: set[str], maximum_bytes: int) -> dict:
    if type(raw) is not str or not raw:
        raise BrowserWorkerProtocolError("message must be non-empty JSON text")
    try:
        encoded = raw.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise BrowserWorkerProtocolError("message is not valid UTF-8") from exc
    if len(encoded) > maximum_bytes:
        raise BrowserWorkerProtocolError("message exceeds its byte limit")

    def no_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=no_duplicate_keys)
    except (json.JSONDecodeError, ValueError) as exc:
        raise BrowserWorkerProtocolError("message is not unambiguous JSON") from exc
    if type(value) is not dict or set(value) != required:
        raise BrowserWorkerProtocolError("message has missing or unknown fields")
    return value


@dataclass(frozen=True)
class BrowserTaskRequest:
    task_id: str
    policy_revision: str
    url: str
    timeout_ms: int = 10_000
    max_text_chars: int = 8_000

    def __post_init__(self) -> None:
        if type(self.task_id) is not str or not _TASK_ID_RE.fullmatch(self.task_id):
            raise BrowserWorkerProtocolError("task_id is invalid")
        if type(self.policy_revision) is not str or not _TOKEN_RE.fullmatch(self.policy_revision):
            raise BrowserWorkerProtocolError("policy_revision is invalid")
        _validate_public_https_url(self.url)
        if type(self.timeout_ms) is not int or not _MIN_TIMEOUT_MS <= self.timeout_ms <= _MAX_TIMEOUT_MS:
            raise BrowserWorkerProtocolError("timeout_ms is outside the permitted range")
        if type(self.max_text_chars) is not int or not 256 <= self.max_text_chars <= MAX_TEXT_CHARS:
            raise BrowserWorkerProtocolError("max_text_chars is outside the permitted range")

    def to_json(self) -> str:
        payload = {
            "task_id": self.task_id,
            "policy_revision": self.policy_revision,
            "url": self.url,
            "timeout_ms": self.timeout_ms,
            "max_text_chars": self.max_text_chars,
        }
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise BrowserWorkerProtocolError("request exceeds its byte limit")
        return encoded

    @classmethod
    def from_json(cls, raw: str) -> "BrowserTaskRequest":
        data = _strict_object(
            raw,
            {"task_id", "policy_revision", "url", "timeout_ms", "max_text_chars"},
            MAX_REQUEST_BYTES,
        )
        return cls(**data)


@dataclass(frozen=True)
class BrowserTaskResponse:
    task_id: str
    outcome: str
    final_url: str
    http_status: int | None
    title: str
    text: str
    elapsed_ms: int

    def __post_init__(self) -> None:
        if type(self.task_id) is not str or not _TASK_ID_RE.fullmatch(self.task_id):
            raise BrowserWorkerProtocolError("task_id is invalid")
        if type(self.outcome) is not str or self.outcome not in _OUTCOMES:
            raise BrowserWorkerProtocolError("outcome is invalid")
        _validate_public_https_url(self.final_url, "final_url")
        if self.http_status is not None and (
            type(self.http_status) is not int or not 100 <= self.http_status <= 599
        ):
            raise BrowserWorkerProtocolError("http_status is invalid")
        _bounded_text(self.title, "title", 500)
        _bounded_text(self.text, "text", MAX_TEXT_CHARS)
        if type(self.elapsed_ms) is not int or not 0 <= self.elapsed_ms <= 120_000:
            raise BrowserWorkerProtocolError("elapsed_ms is invalid")
        if self.outcome != "ok" and (self.http_status is not None or self.title or self.text):
            raise BrowserWorkerProtocolError("non-success responses must not carry page content")

    def to_json(self) -> str:
        payload = {
            "task_id": self.task_id,
            "outcome": self.outcome,
            "final_url": self.final_url,
            "http_status": self.http_status,
            "title": self.title,
            "text": self.text,
            "elapsed_ms": self.elapsed_ms,
        }
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise BrowserWorkerProtocolError("response exceeds its byte limit")
        return encoded

    @classmethod
    def from_json(cls, raw: str) -> "BrowserTaskResponse":
        data = _strict_object(
            raw,
            {"task_id", "outcome", "final_url", "http_status", "title", "text", "elapsed_ms"},
            MAX_RESPONSE_BYTES,
        )
        return cls(**data)
