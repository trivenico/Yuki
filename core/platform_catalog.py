"""Trusted, fail-closed seed catalog for reviewed earning platforms.

Platform entries describe reviewed policy boundaries only. They do not enable
connections, create credentials, or install network adapters.
"""
from datetime import date

from core.platform_registry import PlatformAction, PlatformCapability, PlatformRegistry


def build_platform_registry() -> PlatformRegistry:
    """Return reviewed platform metadata with all external execution disabled."""
    return PlatformRegistry((
        PlatformCapability(
            platform_id="upwork",
            display_name="Upwork",
            canonical_domains=("upwork.com", "mcp.upwork.com"),
            categories=("freelance_marketplace",),
            # Do not infer regional eligibility from the user's location.
            supported_regions=(),
            actions=(
                PlatformAction(
                    "read_opportunities",
                    automation_mode="official_api",
                    required_scopes=(),
                    owner_approval_required=False,
                ),
                PlatformAction(
                    "draft_proposal",
                    automation_mode="draft_only",
                    owner_approval_required=False,
                ),
                PlatformAction("submit_proposal", automation_mode="unsupported"),
                PlatformAction("send_message", automation_mode="unsupported"),
                PlatformAction("accept_contract", automation_mode="unsupported"),
                PlatformAction("fund_escrow", automation_mode="unsupported"),
                PlatformAction("approve_milestone", automation_mode="unsupported"),
                PlatformAction("withdraw", automation_mode="unsupported"),
                PlatformAction("change_payout_details", automation_mode="unsupported"),
            ),
            policy_url="https://www.upwork.com/ai/mcp",
            policy_reviewed_on=date(2026, 11, 11),
            policy_recheck_by=date(2026, 11, 18),
            adapter_version="official-mcp-review-1",
            # Explicitly disabled until redirect URI approval, provider OAuth
            # composition and the owner-controlled tool allow-list are complete.
            enabled=False,
        ),
    ))
