"""Immutable host-side registry for prepared OAuth providers.

Only providers successfully prepared by MCPProviderOAuthBootstrap may enter this
registry. It exposes the narrow resolver/catalog interfaces consumed by the
authenticated OAuth API; endpoints, client IDs, and credentials stay private.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Mapping

from core.mcp_oauth_provider_bootstrap import PreparedOAuthProvider

_PROVIDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")


@dataclass(frozen=True)
class OAuthProviderPresentation:
    display_name: str
    description: str = ""

    def __post_init__(self):
        if (not isinstance(self.display_name, str) or not self.display_name.strip()
                or len(self.display_name) > 100
                or any(ord(char) < 0x20 or ord(char) == 0x7f for char in self.display_name)):
            raise ValueError("provider display_name is invalid")
        if (not isinstance(self.description, str) or len(self.description) > 240
                or any(ord(char) < 0x20 and char not in "\t\n" for char in self.description)
                or any(ord(char) == 0x7f for char in self.description)):
            raise ValueError("provider description is invalid")


class PreparedOAuthProviderRegistry:
    """Publish only fully prepared, explicitly presented provider integrations."""

    def __init__(
        self,
        providers: Mapping[str, PreparedOAuthProvider],
        presentations: Mapping[str, OAuthProviderPresentation],
    ):
        if not isinstance(providers, Mapping) or not isinstance(presentations, Mapping):
            raise TypeError("provider and presentation mappings are required")
        if not providers or set(providers) != set(presentations):
            raise ValueError("each prepared provider requires exactly one presentation")
        services = {}
        catalog = []
        for provider_id, prepared in providers.items():
            if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
                raise ValueError("provider_id is invalid")
            if not isinstance(prepared, PreparedOAuthProvider):
                raise TypeError("only successfully prepared OAuth providers may be registered")
            if prepared.provider_id != provider_id:
                raise ValueError("prepared provider identity does not match registry key")
            presentation = presentations[provider_id]
            if not isinstance(presentation, OAuthProviderPresentation):
                raise TypeError("trusted provider presentation is required")
            service = prepared.authorization_service
            if not callable(getattr(service, "begin", None)) or not callable(
                getattr(service, "complete", None)
            ):
                raise TypeError("prepared provider authorization service is invalid")
            services[provider_id] = service
            catalog.append({
                "provider_id": provider_id,
                "display_name": presentation.display_name,
                "description": presentation.description,
                "available": True,
            })
        self._services = MappingProxyType(services)
        self._catalog = tuple(
            MappingProxyType(item) for item in sorted(catalog, key=lambda item: item["provider_id"])
        )

    def authorization_service_for_provider(self, provider_id: str):
        """Resolver compatible with AuthenticatedOAuthAuthorizationAPI."""
        if not isinstance(provider_id, str):
            return None
        return self._services.get(provider_id)

    def provider_catalog(self):
        """Return safe catalog projections; no URLs, issuer, client ID, or secrets."""
        return tuple(dict(item) for item in self._catalog)

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(item["provider_id"] for item in self._catalog)
