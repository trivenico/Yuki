"""Fail-closed, read-only discovery boundary for reviewed income platforms.

Adapters are injected trusted implementations. This module performs no network
requests and never receives credentials; adapters must use their own approved
official-API transport and return source-attributed candidates.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from core.platform_registry import PlatformRegistry


_ACTION = "read_opportunities"
_QUERY_MAX = 300
_TITLE_MAX = 200
_SUMMARY_MAX = 3000
_ID_MAX = 200
_REGION = re.compile(r"^[A-Z]{2}$")


class DiscoveryError(ValueError):
    """Stable, non-sensitive discovery boundary error."""


@dataclass(frozen=True)
class OpportunityCandidate:
    external_id: str
    title: str
    category: str
    source_url: str
    summary: str = ""
    observed_at: datetime | None = None


class ReadOnlyOpportunityAdapter(Protocol):
    """Trusted adapter contract; credentials must never be passed in arguments."""

    def discover(
        self, *, query: str, region: str, limit: int
    ) -> Sequence[OpportunityCandidate]:
        """Fetch public opportunities using the platform's approved read API."""


@dataclass(frozen=True)
class DiscoveryBatch:
    platform_id: str
    adapter_version: str
    region: str
    candidates: tuple[OpportunityCandidate, ...]
    retrieved_at: datetime


class IncomeOpportunityDiscoveryService:
    """Run only explicitly registered, current-policy official read adapters."""

    def __init__(
        self,
        registry: PlatformRegistry,
        adapters: Mapping[str, ReadOnlyOpportunityAdapter],
    ):
        if not isinstance(registry, PlatformRegistry):
            raise TypeError("registry_must_be_platform_registry")
        if not isinstance(adapters, Mapping):
            raise TypeError("adapters_must_be_mapping")
        normalized = dict(adapters)
        for platform_id, adapter in normalized.items():
            if not isinstance(platform_id, str) or not platform_id:
                raise ValueError("invalid_adapter_platform_id")
            if not callable(getattr(adapter, "discover", None)):
                raise TypeError("adapter_missing_discover")
        self._registry = registry
        self._adapters = normalized

    def discover(
        self,
        platform_id: str,
        *,
        query: str = "",
        region: str,
        limit: int = 20,
        on_date=None,
    ) -> DiscoveryBatch:
        if not isinstance(query, str) or len(query) > _QUERY_MAX or any(
            ord(char) < 32 for char in query
        ):
            raise DiscoveryError("invalid_discovery_query")
        if not isinstance(region, str) or not _REGION.fullmatch(region):
            raise DiscoveryError("invalid_discovery_region")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise DiscoveryError("invalid_discovery_limit")

        platform = self._registry.get(platform_id)
        if platform is None:
            raise DiscoveryError("platform_not_available")
        if region not in platform.supported_regions:
            raise DiscoveryError("platform_region_not_supported")
        if self._registry.effective_mode(platform_id, _ACTION, on_date) != "official_api":
            raise DiscoveryError("platform_read_access_not_approved")

        adapter = self._adapters.get(platform_id)
        if adapter is None:
            raise DiscoveryError("platform_adapter_not_installed")
        try:
            raw = adapter.discover(query=query.strip(), region=region, limit=limit)
        except Exception as exc:
            # Avoid propagating provider responses, URLs, headers or secret values.
            raise DiscoveryError("platform_discovery_failed") from exc
        if not isinstance(raw, (list, tuple)) or len(raw) > limit:
            raise DiscoveryError("invalid_adapter_response")

        candidates: list[OpportunityCandidate] = []
        seen: set[str] = set()
        for item in raw:
            candidate = self._validate_candidate(item, platform.canonical_domains)
            if candidate.external_id in seen:
                continue
            seen.add(candidate.external_id)
            candidates.append(candidate)

        return DiscoveryBatch(
            platform_id=platform_id,
            adapter_version=platform.adapter_version or "unversioned",
            region=region,
            candidates=tuple(candidates),
            retrieved_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _validate_candidate(item, canonical_domains) -> OpportunityCandidate:
        if not isinstance(item, OpportunityCandidate):
            raise DiscoveryError("invalid_adapter_candidate")
        if (
            not isinstance(item.external_id, str)
            or not item.external_id.strip()
            or len(item.external_id) > _ID_MAX
            or any(ord(char) < 32 for char in item.external_id)
        ):
            raise DiscoveryError("invalid_candidate_id")
        if (
            not isinstance(item.title, str)
            or not item.title.strip()
            or len(item.title) > _TITLE_MAX
            or any(ord(char) < 32 for char in item.title)
        ):
            raise DiscoveryError("invalid_candidate_title")
        if (
            not isinstance(item.category, str)
            or not item.category.strip()
            or len(item.category) > 80
        ):
            raise DiscoveryError("invalid_candidate_category")
        if (
            not isinstance(item.summary, str)
            or len(item.summary) > _SUMMARY_MAX
        ):
            raise DiscoveryError("invalid_candidate_summary")
        if item.observed_at is not None and (
            not isinstance(item.observed_at, datetime)
            or item.observed_at.tzinfo is None
            or item.observed_at.utcoffset() is None
        ):
            raise DiscoveryError("candidate_timestamp_must_be_timezone_aware")

        try:
            parsed = urlsplit(item.source_url)
            hostname = (parsed.hostname or "").lower().rstrip(".")
            port = parsed.port
        except (TypeError, ValueError):
            raise DiscoveryError("invalid_candidate_source_url") from None
        if (
            not isinstance(item.source_url, str)
            or len(item.source_url) > 2048
            or parsed.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or not any(
                hostname == domain or hostname.endswith("." + domain)
                for domain in canonical_domains
            )
        ):
            raise DiscoveryError("candidate_source_outside_platform_domains")
        return OpportunityCandidate(
            external_id=item.external_id.strip(),
            title=item.title.strip(),
            category=item.category.strip(),
            source_url=item.source_url,
            summary=item.summary,
            observed_at=item.observed_at,
        )
