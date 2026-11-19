"""Privacy-filtered desktop metadata source.

This adapter accepts already-collected application/domain identifiers from a
platform collector. It never reads window titles, screenshots, clipboard,
keystrokes, page text, process arguments, or file paths.
"""
from __future__ import annotations

import re
from itertools import islice
from typing import Optional

from core.observation_sources import FakeObservationSource, ObservationEvent


_DOMAIN = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z")


def _bounded_allowlist(values, label):
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label} must be a collection of identifiers")
    try:
        iterator = iter(values)
        items = tuple(islice(iterator, 257))
    except TypeError as exc:
        raise ValueError(f"{label} must be iterable") from exc
    if len(items) > 256:
        raise ValueError(f"{label} may contain at most 256 entries")
    clean = set()
    for item in items:
        if not isinstance(item, str):
            raise ValueError(f"{label} entries must be text")
        value = item.strip().lower()
        if not value or len(value) > 253 or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,252}", value):
            raise ValueError(f"{label} contains an invalid identifier")
        clean.add(value)
    return frozenset(clean)


class DesktopMetadataAdapter:
    """Bounded source for allowlisted app IDs and browser domains only.

    A platform-specific collector must obtain OS permission and pass these
    identifiers explicitly. An empty allowlist captures nothing.
    """

    source_id = "desktop:metadata"

    def __init__(
        self,
        *,
        allowed_app_ids=(),
        allowed_browser_domains=(),
        capacity: int = 64,
    ):
        self._apps = _bounded_allowlist(allowed_app_ids, "allowed_app_ids")
        raw_domains = _bounded_allowlist(allowed_browser_domains, "allowed_browser_domains")
        domains = []
        for domain in raw_domains:
            normalized = domain.rstrip(".")
            if not _DOMAIN.fullmatch(normalized):
                raise ValueError("browser domain allowlist contains an invalid domain")
            domains.append(normalized)
        self._domains = frozenset(domains)
        self._queue = FakeObservationSource(self.source_id, capacity=capacity)
        self._closed = False

    def publish_app_activity(self, application_id: str) -> bool:
        if self._closed:
            return False
        if not isinstance(application_id, str):
            raise ValueError("application_id must be text")
        normalized = application_id.strip().lower()
        if normalized not in self._apps:
            return False
        return self._queue.publish(ObservationEvent(
            self.source_id, "app_activity", {"application_id": normalized},
        ))

    def publish_browser_domain(self, domain: str) -> bool:
        if self._closed:
            return False
        if not isinstance(domain, str):
            raise ValueError("domain must be text")
        normalized = domain.strip().lower().rstrip(".")
        if not _DOMAIN.fullmatch(normalized) or normalized not in self._domains:
            return False
        return self._queue.publish(ObservationEvent(
            self.source_id, "browser_domain", {"domain": normalized},
        ))

    def read_next(self, timeout: float = 0.0) -> Optional[ObservationEvent]:
        return self._queue.read_next(timeout)

    def close(self) -> None:
        self._closed = True
        self._queue.close()
