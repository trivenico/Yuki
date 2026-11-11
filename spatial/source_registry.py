"""Validated metadata contracts for future spatial data adapters.

This module performs no network access or rendering. Endpoint validation is a
sanity check, not an SSRF defense; future adapters must enforce egress policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import ipaddress
import re
from typing import Iterable
from urllib.parse import urlsplit

_ID = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_RECORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class Freshness(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    ESTIMATED = "estimated"
    REPLAYED = "replayed"
    UNKNOWN = "unknown"


class Permission(str, Enum):
    ALLOWED = "allowed"
    PROHIBITED = "prohibited"
    UNKNOWN = "unknown"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    OWNER_PRIVATE = "owner_private"
    RESTRICTED = "restricted"


@dataclass(frozen=True)
class LicensePolicy:
    license_id: str
    attribution: str = ""
    commercial_use: Permission = Permission.UNKNOWN
    redistribution: Permission = Permission.UNKNOWN
    retention_days: int | None = None

    def validate(self) -> list[str]:
        errors = []
        if not isinstance(self.license_id, str) or not self.license_id.strip() or len(self.license_id) > 160:
            errors.append("license_id must be non-empty text of at most 160 characters")
        if not isinstance(self.attribution, str) or len(self.attribution) > 1000:
            errors.append("attribution must be text of at most 1000 characters")
        if not isinstance(self.commercial_use, Permission) or not isinstance(self.redistribution, Permission):
            errors.append("license permissions must use Permission values")
        if self.retention_days is not None and (
            isinstance(self.retention_days, bool) or not isinstance(self.retention_days, int)
            or not 0 <= self.retention_days <= 36500
        ):
            errors.append("retention_days must be between 0 and 36500 or None")
        return errors


@dataclass(frozen=True)
class SpatialSource:
    source_id: str
    provider: str
    version: str
    domain: str
    schema_version: str
    license: LicensePolicy
    endpoint_origin: str | None = None
    sensitivity: Sensitivity = Sensitivity.PUBLIC
    max_response_bytes: int = 2_000_000
    max_entities: int = 10_000
    timeout_seconds: float = 10.0
    refresh_interval_seconds: int = 60
    stale_after_seconds: int = 300
    enabled: bool = False


def _origin_errors(origin: str | None) -> list[str]:
    if origin is None:
        return []
    if not isinstance(origin, str) or len(origin) > 300:
        return ["endpoint_origin must be an HTTPS origin of at most 300 characters"]
    try:
        parsed = urlsplit(origin)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or parsed.port not in (None, 443)):
            return ["endpoint_origin must be a credential-free HTTPS origin without path, query, or fragment"]
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return ["endpoint_origin must not target a non-public IP address"]
        if host.lower() in {"localhost", "localhost.localdomain"} or host.lower().endswith(".local"):
            return ["endpoint_origin must not target a local hostname"]
    except ValueError:
        return ["endpoint_origin is malformed"]
    return []


def validate_source(source: SpatialSource) -> list[str]:
    if not isinstance(source, SpatialSource):
        return ["source must be a SpatialSource"]
    errors = []
    if not isinstance(source.source_id, str) or not _ID.fullmatch(source.source_id):
        errors.append("source_id must be a lowercase identifier of at most 64 characters")
    for field, value, maximum in (
        ("provider", source.provider, 160), ("version", source.version, 80),
        ("domain", source.domain, 80), ("schema_version", source.schema_version, 80),
    ):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            errors.append(f"{field} must be non-empty text of at most {maximum} characters")
    if not isinstance(source.license, LicensePolicy):
        errors.append("license must be a LicensePolicy")
    else:
        errors.extend(source.license.validate())
    if not isinstance(source.sensitivity, Sensitivity):
        errors.append("sensitivity must be a Sensitivity value")
    errors.extend(_origin_errors(source.endpoint_origin))
    for field, value, low, high in (
        ("max_response_bytes", source.max_response_bytes, 1, 50_000_000),
        ("max_entities", source.max_entities, 1, 1_000_000),
        ("refresh_interval_seconds", source.refresh_interval_seconds, 1, 86_400),
        ("stale_after_seconds", source.stale_after_seconds, 1, 2_592_000),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            errors.append(f"{field} must be between {low} and {high}")
    if (isinstance(source.timeout_seconds, bool) or not isinstance(source.timeout_seconds, (int, float))
            or not 0.1 <= source.timeout_seconds <= 120):
        errors.append("timeout_seconds must be between 0.1 and 120")
    if (isinstance(source.refresh_interval_seconds, int)
            and isinstance(source.stale_after_seconds, int)
            and source.refresh_interval_seconds > source.stale_after_seconds):
        errors.append("refresh_interval_seconds must not exceed stale_after_seconds")
    if not isinstance(source.enabled, bool):
        errors.append("enabled must be boolean")
    return errors


def _timestamp(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class SpatialRecord:
    source_id: str
    record_id: str
    observed_at: str
    fetched_at: str
    published_at: str | None = None
    freshness: Freshness = Freshness.UNKNOWN
    confidence: float | None = None
    is_estimated: bool = False

    def validate(self, registry: "SpatialSourceRegistry") -> list[str]:
        if registry.get(self.source_id) is None:
            return ["record references an unregistered source"]
        errors = []
        if not isinstance(self.record_id, str) or not _RECORD.fullmatch(self.record_id):
            errors.append("record_id must be a stable identifier of at most 256 characters")
        try:
            observed = _timestamp(self.observed_at, "observed_at")
            fetched = _timestamp(self.fetched_at, "fetched_at")
            if observed > fetched:
                errors.append("observed_at must not be later than fetched_at")
            if self.published_at is not None and _timestamp(self.published_at, "published_at") > fetched:
                errors.append("published_at must not be later than fetched_at")
        except ValueError as exc:
            errors.append(str(exc))
        if not isinstance(self.freshness, Freshness):
            errors.append("freshness must be a Freshness value")
        if self.confidence is not None and (
            isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float))
            or not 0 <= self.confidence <= 1
        ):
            errors.append("confidence must be between 0 and 1 or None")
        if not isinstance(self.is_estimated, bool):
            errors.append("is_estimated must be boolean")
        if self.is_estimated and self.freshness == Freshness.CURRENT:
            errors.append("estimated records must not be labelled current")
        return errors


class SpatialSourceRegistry:
    """In-memory registry with no network, persistence, or device-control effects."""

    def __init__(self, sources: Iterable[SpatialSource] = ()) -> None:
        self._sources: dict[str, SpatialSource] = {}
        for source in sources:
            self.register(source)

    def register(self, source: SpatialSource) -> None:
        errors = validate_source(source)
        if errors:
            raise ValueError("; ".join(errors))
        if source.source_id in self._sources:
            raise ValueError("source_id is already registered")
        self._sources[source.source_id] = source

    def get(self, source_id: str) -> SpatialSource | None:
        return self._sources.get(source_id)

    def list_sources(self, *, enabled_only: bool = False) -> tuple[SpatialSource, ...]:
        return tuple(s for s in self._sources.values() if not enabled_only or s.enabled)
