"""Validated provider capability metadata and deterministic routing policy.

This module describes known adapter-level capabilities, not undocumented
capabilities of individual remote models. Unknown is always safer than guessing.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
from typing import Iterable, Mapping


_ALLOWED_CAPABILITIES = frozenset({
    "text", "local_inference", "per_task_routing", "official_cli",
    "model_discovery", "multi_provider_routing",
})
_LOCAL_PRIVACY = frozenset({"local", "local_runtime", "local_only"})
_CLOUD_PRIVACY = frozenset({"cloud_provider", "third_party_gateway", "task_policy"})
_KNOWN_PRIVACY = _LOCAL_PRIVACY | _CLOUD_PRIVACY | frozenset({"local_or_configured_endpoint", "unknown"})
_MAX_REQUIRED_CAPABILITIES = 6
_MAX_PREFERENCES = 32


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    capabilities: frozenset[str]
    implemented: bool
    ready: bool
    privacy: str
    configured_model: str | None = None
    reason: str = ""

    def __post_init__(self):
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("provider must be a non-empty string")
        if self.provider != self.provider.strip().lower():
            raise ValueError("provider must be normalized to lowercase")
        if not isinstance(self.capabilities, frozenset):
            raise TypeError("capabilities must be a frozenset")
        unknown = self.capabilities - _ALLOWED_CAPABILITIES
        if unknown:
            raise ValueError("unknown provider capabilities: " + ", ".join(sorted(unknown)))
        if not isinstance(self.implemented, bool) or not isinstance(self.ready, bool):
            raise TypeError("implemented and ready must be booleans")
        if self.ready and not self.implemented:
            raise ValueError("an unimplemented provider cannot be ready")
        if not isinstance(self.privacy, str) or self.privacy not in _KNOWN_PRIVACY:
            raise ValueError("privacy must use a recognized classification")
        if self.configured_model is not None and (
            not isinstance(self.configured_model, str) or not self.configured_model.strip()
        ):
            raise ValueError("configured_model must be None or non-empty text")


class CapabilityRegistry:
    """In-memory registry; callers supply sanitized entries from local config."""

    def __init__(self, providers: Iterable[ProviderCapabilities] = ()):
        self._providers: dict[str, ProviderCapabilities] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: ProviderCapabilities) -> None:
        if not isinstance(provider, ProviderCapabilities):
            raise TypeError("provider must be a ProviderCapabilities instance")
        key = provider.provider
        if key in self._providers:
            raise ValueError(f"provider already registered: {key}")
        self._providers[key] = provider

    def get(self, name: str) -> ProviderCapabilities | None:
        return self._providers.get(name.strip().lower()) if isinstance(name, str) else None

    def list(self) -> list[dict]:
        return [
            {
                "provider": item.provider,
                "capabilities": sorted(item.capabilities),
                "implemented": item.implemented,
                "ready": item.ready,
                "privacy": item.privacy,
                "configured_model": item.configured_model,
                "reason": item.reason,
            }
            for item in sorted(self._providers.values(), key=lambda p: p.provider)
        ]

    def route(
        self,
        *,
        required: Iterable[str] = ("text",),
        preference: Iterable[str] = (),
        allow_cloud: bool = False,
        strict_preference: bool = False,
    ) -> ProviderCapabilities:
        """Select a ready adapter without probing networks or silently changing policy.

        Preference is a caller-supplied ordering. By default, providers outside
        that list are considered afterward alphabetically; strict_preference turns
        the list into an allowlist. Cloud adapters are excluded unless the caller
        explicitly opts in.
        """
        if isinstance(required, (str, bytes)):
            raise TypeError("required must be an iterable of capability names, not text")
        if isinstance(preference, (str, bytes)):
            raise TypeError("preference must be an iterable of provider names, not text")
        if type(allow_cloud) is not bool:
            raise TypeError("allow_cloud must be a boolean")
        if type(strict_preference) is not bool:
            raise TypeError("strict_preference must be a boolean")
        try:
            required_values = tuple(islice(iter(required), _MAX_REQUIRED_CAPABILITIES + 1))
            preference_values = tuple(islice(iter(preference), _MAX_PREFERENCES + 1))
        except TypeError as exc:
            raise TypeError("required and preference must be iterable") from exc
        if len(required_values) > _MAX_REQUIRED_CAPABILITIES:
            raise ValueError("too many required capabilities")
        if len(preference_values) > _MAX_PREFERENCES:
            raise ValueError("too many provider preferences")
        required_set = frozenset(required_values)
        if any(not isinstance(item, str) for item in required_set):
            raise ValueError("required capabilities must be strings")
        unknown = required_set - _ALLOWED_CAPABILITIES
        if unknown:
            raise ValueError("unknown required capabilities: " + ", ".join(sorted(unknown)))
        preferences = []
        seen = set()
        for name in preference_values:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("preference entries must be non-empty provider names")
            normalized = name.strip().lower()
            if normalized not in seen:
                preferences.append(normalized)
                seen.add(normalized)
        order = {name: i for i, name in enumerate(preferences)}
        candidates = [
            item for item in self._providers.values()
            if item.implemented and item.ready
            and (not strict_preference or item.provider in seen)
            and required_set.issubset(item.capabilities)
            and (
                item.privacy in _LOCAL_PRIVACY
                or (allow_cloud and item.privacy in _CLOUD_PRIVACY)
            )
        ]
        candidates.sort(key=lambda p: (
            order.get(p.provider, len(order)),
            0 if p.privacy in _LOCAL_PRIVACY else 1,
            p.provider,
        ))
        if not candidates:
            raise LookupError("no ready provider satisfies the requested capabilities and privacy policy")
        return candidates[0]


def registry_from_catalog(catalog: Iterable[Mapping]) -> CapabilityRegistry:
    """Convert the existing sanitized provider catalog without trusting extra fields."""
    entries = []
    for item in catalog:
        if not isinstance(item, Mapping):
            raise TypeError("catalog entries must be mappings")
        name = item.get("name")
        if not isinstance(name, str):
            raise ValueError("catalog provider name is missing")
        caps = item.get("capabilities", [])
        if not isinstance(caps, list) or any(not isinstance(cap, str) for cap in caps):
            raise ValueError(f"invalid capabilities for provider {name}")
        # Only allow capabilities understood by this router. Catalog hints such
        # as "multimodal_model_dependent" are descriptive, not guaranteed adapter
        # capabilities, so they are intentionally not routable requirements.
        safe_caps = frozenset(cap for cap in caps if cap in _ALLOWED_CAPABILITIES)
        entries.append(ProviderCapabilities(
            provider=name.strip().lower(),
            capabilities=safe_caps,
            implemented=item.get("implemented") is True,
            ready=item.get("ready") is True,
            privacy=str(item.get("privacy", "unknown")),
            configured_model=item.get("configured_model"),
            reason=str(item.get("status", "")),
        ))
    return CapabilityRegistry(entries)
