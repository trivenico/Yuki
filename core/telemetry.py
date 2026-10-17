"""Privacy-conscious telemetry primitives with optional OpenTelemetry support.

No prompts, completions, credentials, or tool arguments are recorded by default.
Install opentelemetry-api and configure an SDK/exporter separately to export spans.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import logging
import os
import re
from pathlib import Path
import threading
import time
from typing import Iterator

_LOG = logging.getLogger("yuki.telemetry")


@dataclass(frozen=True)
class TelemetryEvent:
    name: str
    started_at: str
    duration_ms: float
    ok: bool
    attributes: dict[str, str | int | float | bool]


class Telemetry:
    """Record bounded, redacted event metadata; tracing is optional."""

    def __init__(self, *, enabled: bool = True, event_log: str | Path | None = None,
                 service_name: str = "yuki"):
        self.enabled = bool(enabled)
        self.service_name = self._safe(service_name, 80)
        self.event_log = Path(event_log).expanduser() if event_log else None
        self._lock = threading.RLock()
        self._events: list[TelemetryEvent] = []
        self._tracer = None
        if self.enabled:
            try:
                from opentelemetry import trace
                self._tracer = trace.get_tracer(self.service_name)
            except ImportError:
                self._tracer = None

    @staticmethod
    def _safe(value: object, limit: int = 120) -> str:
        text = str(value).replace("\n", " ").replace("\r", " ")
        return text[:limit]

    @staticmethod
    def _attributes(attributes: dict | None) -> dict[str, str | int | float | bool]:
        safe: dict[str, str | int | float | bool] = {}
        for key, value in (attributes or {}).items():
            key = str(key)[:80]
            # Drop likely secret/prompt payload fields entirely.
            if any(part in key.lower() for part in ("prompt", "completion", "secret", "token", "password", "credential", "argument", "content")):
                continue
            if isinstance(value, bool) or isinstance(value, (int, float)):
                safe[key] = value
            elif isinstance(value, str):
                # Redact credential-shaped values even if a caller puts them in
                # an innocently named attribute.
                cleaned = re.sub(
                    r"""(?i)(api[_-]?key|access[_-]?token|password|client_secret)\s*[:=]\s*[^\s,;}'"]+""",
                    r"\1=[REDACTED]", value,
                )
                cleaned = re.sub(
                    r"-----BEGIN (?:RSA |EC |OPENSSH |PRIVATE )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH |PRIVATE )?PRIVATE KEY-----",
                    "[PRIVATE KEY REDACTED]", cleaned, flags=re.I | re.S,
                )
                safe[key] = cleaned[:160]
        return safe

    @contextmanager
    def span(self, name: str, *, attributes: dict | None = None) -> Iterator[dict]:
        metadata = self._attributes(attributes)
        if not self.enabled:
            yield {"set_attribute": lambda *_: None}
            return
        started = time.perf_counter()
        timestamp = datetime.now(timezone.utc).isoformat()
        ok = False
        span_cm = self._tracer.start_as_current_span(self._safe(name)) if self._tracer else None
        try:
            if span_cm:
                span = span_cm.__enter__()
                for key, value in metadata.items():
                    span.set_attribute(key, value)
                class SpanHandle:
                    def set_attribute(self, key, value):
                        clean = self_outer._attributes({key: value})
                        metadata.update(clean)
                        for safe_key, safe_value in clean.items():
                            span.set_attribute(safe_key, safe_value)
                self_outer = self
                handle = SpanHandle()
            else:
                handle = {"set_attribute": lambda key, value: metadata.update(self._attributes({key: value}))}
            yield handle
            ok = True
        except Exception as exc:
            if span_cm and self._tracer:
                span.record_exception(exc)
                span.set_status(__import__("opentelemetry.trace", fromlist=["Status","StatusCode"]).Status(
                    __import__("opentelemetry.trace", fromlist=["Status","StatusCode"]).StatusCode.ERROR))
            raise
        finally:
            if span_cm:
                span_cm.__exit__(None, None, None)
            event = TelemetryEvent(self._safe(name), timestamp,
                                   round((time.perf_counter() - started) * 1000, 3), ok, metadata)
            with self._lock:
                self._events.append(event)
                self._events = self._events[-5000:]
                if self.event_log:
                    self.event_log.parent.mkdir(parents=True, exist_ok=True)
                    with self.event_log.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [asdict(item) for item in self._events]

    @property
    def tracing_available(self) -> bool:
        return self._tracer is not None
