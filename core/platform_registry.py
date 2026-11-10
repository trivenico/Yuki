"""Fail-closed registry for reviewed online income platform capabilities.

The registry stores capability metadata only. It never stores credentials or
performs network requests. Unreviewed and stale integrations are unsupported.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from types import MappingProxyType
from typing import Iterable, Mapping


AUTOMATION_MODES = frozenset({
    "official_api",
    "user_present_browser",
    "draft_only",
    "unsupported",
})
_MUTATING_ACTIONS = frozenset({
    "submit_application",
    "submit_proposal",
    "publish_listing",
    "publish_content",
    "send_message",
    "accept_contract",
    "change_price",
    "issue_refund",
    "purchase",
    "withdraw",
    "change_payout_details",
    "accept_legal_terms",
})
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,99}$")
_DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


@dataclass(frozen=True)
class PlatformAction:
    action_id: str
    automation_mode: str = "unsupported"
    required_scopes: tuple[str, ...] = ()
    owner_approval_required: bool = True
    idempotency_required: bool = True

    def __post_init__(self):
        if not isinstance(self.action_id, str) or not _ID.fullmatch(self.action_id):
            raise ValueError("invalid_platform_action_id")
        if self.automation_mode not in AUTOMATION_MODES:
            raise ValueError("invalid_platform_automation_mode")
        if type(self.owner_approval_required) is not bool or type(self.idempotency_required) is not bool:
            raise ValueError("invalid_platform_action_flags")
        if self.action_id in _MUTATING_ACTIONS and not self.owner_approval_required:
            raise ValueError("externally_visible_or_financial_action_requires_owner_approval")
        scopes = tuple(self.required_scopes)
        if len(scopes) > 32 or any(
            not isinstance(scope, str) or not scope.strip() or len(scope) > 128
            for scope in scopes
        ):
            raise ValueError("invalid_platform_action_scopes")
        object.__setattr__(self, "required_scopes", scopes)


@dataclass(frozen=True)
class PlatformCapability:
    platform_id: str
    display_name: str
    canonical_domains: tuple[str, ...]
    categories: tuple[str, ...]
    supported_regions: tuple[str, ...]
    actions: tuple[PlatformAction, ...]
    policy_url: str | None = None
    policy_reviewed_on: date | None = None
    policy_recheck_by: date | None = None
    adapter_version: str | None = None
    enabled: bool = False
    credential_reference: str | None = None

    def __post_init__(self):
        if not isinstance(self.platform_id, str) or not _ID.fullmatch(self.platform_id):
            raise ValueError("invalid_platform_id")
        if not isinstance(self.display_name, str) or not self.display_name.strip() or len(self.display_name) > 120:
            raise ValueError("invalid_platform_display_name")
        domains = tuple(domain.lower() for domain in self.canonical_domains)
        if not domains or len(domains) > 20 or any(not _DOMAIN.fullmatch(domain) for domain in domains):
            raise ValueError("invalid_platform_domains")
        object.__setattr__(self, "canonical_domains", domains)
        for name, values, limit in (
            ("categories", self.categories, 20),
            ("supported_regions", self.supported_regions, 100),
        ):
            normalized = tuple(values)
            if len(normalized) > limit or any(
                not isinstance(value, str) or not value.strip() or len(value) > 80
                for value in normalized
            ):
                raise ValueError("invalid_platform_" + name)
            object.__setattr__(self, name, normalized)
        actions = tuple(self.actions)
        if len(actions) > 100 or any(not isinstance(item, PlatformAction) for item in actions):
            raise ValueError("invalid_platform_actions")
        if len({item.action_id for item in actions}) != len(actions):
            raise ValueError("duplicate_platform_action")
        object.__setattr__(self, "actions", actions)
        if type(self.enabled) is not bool:
            raise ValueError("invalid_platform_enabled_flag")
        if self.policy_url is not None and (
            not isinstance(self.policy_url, str)
            or not self.policy_url.startswith("https://")
            or len(self.policy_url) > 2048
        ):
            raise ValueError("invalid_platform_policy_url")
        if self.policy_reviewed_on is not None and not isinstance(self.policy_reviewed_on, date):
            raise ValueError("invalid_policy_review_date")
        if self.policy_recheck_by is not None and not isinstance(self.policy_recheck_by, date):
            raise ValueError("invalid_policy_recheck_date")
        if (
            self.policy_reviewed_on is not None
            and self.policy_recheck_by is not None
            and self.policy_recheck_by < self.policy_reviewed_on
        ):
            raise ValueError("policy_recheck_precedes_review")
        if self.adapter_version is not None and (
            not isinstance(self.adapter_version, str) or not self.adapter_version.strip()
            or len(self.adapter_version) > 40
        ):
            raise ValueError("invalid_adapter_version")
        # Never accept a raw credential here. Only opaque secret-store references.
        if self.credential_reference is not None and (
            not isinstance(self.credential_reference, str)
            or not self.credential_reference.startswith("secret://")
            or len(self.credential_reference) > 256
        ):
            raise ValueError("credential_must_be_secret_store_reference")

    def policy_is_current(self, on_date: date | None = None) -> bool:
        today = on_date or date.today()
        return bool(
            self.policy_url
            and self.policy_reviewed_on
            and self.policy_recheck_by
            and self.policy_reviewed_on <= today <= self.policy_recheck_by
        )

    def action(self, action_id: str) -> PlatformAction | None:
        return next((item for item in self.actions if item.action_id == action_id), None)

    def effective_mode(self, action_id: str, on_date: date | None = None) -> str:
        """Return unsupported unless the platform and its policy review are current."""
        item = self.action(action_id)
        if not self.enabled or item is None or not self.policy_is_current(on_date):
            return "unsupported"
        return item.automation_mode


class PlatformRegistry:
    """Immutable-by-snapshot capability index; duplicate IDs fail closed."""

    def __init__(self, platforms: Iterable[PlatformCapability] = ()):
        items: dict[str, PlatformCapability] = {}
        for platform in platforms:
            if not isinstance(platform, PlatformCapability):
                raise TypeError("platform_registry_accepts_platform_capabilities")
            if platform.platform_id in items:
                raise ValueError("duplicate_platform_id")
            items[platform.platform_id] = platform
        self._items: Mapping[str, PlatformCapability] = MappingProxyType(items)

    def get(self, platform_id: str) -> PlatformCapability | None:
        return self._items.get(platform_id)

    def list(self) -> tuple[PlatformCapability, ...]:
        return tuple(self._items.values())

    def effective_mode(
        self, platform_id: str, action_id: str, on_date: date | None = None
    ) -> str:
        platform = self.get(platform_id)
        if platform is None:
            return "unsupported"
        return platform.effective_mode(action_id, on_date)
