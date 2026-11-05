"""Runtime provider readiness diagnostics; never performs inference or fallback."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from providers.adapter_conformance import inspect_provider_adapter


@dataclass(frozen=True)
class ProviderReadiness:
    provider: str
    status: str
    configured: bool
    reachable: bool | None
    live_verified: bool
    capabilities: dict[str, bool]
    verification_level: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_readiness(
    *, provider_name: str, configured: bool, reachable: bool | None,
    live_status: str | None = None,
) -> str:
    """Classify readiness without conflating configuration, reachability and inference."""
    if not configured:
        return "not_configured"
    if reachable is False:
        return "unavailable"
    if live_status == "pass":
        return "live_verified"
    if live_status in {"fail", "partial"}:
        return "live_check_failed" if live_status == "fail" else "partially_verified"
    if reachable is True:
        return "reachable_unverified"
    return "configured_unverified"


def inspect_configured_provider(
    *, config: dict[str, Any], provider_factory: Callable[[dict], Any],
    live_report: dict[str, Any] | None = None,
) -> ProviderReadiness:
    """Inspect one explicitly selected real provider; does not send prompts."""
    if not isinstance(config, dict):
        raise ValueError("provider_configuration_invalid")
    name = str(config.get("provider", "")).strip().lower()
    if not name or name == "mock":
        raise ValueError("real_provider_selection_required")
    provider = provider_factory(config)
    if type(provider).__name__.lower() == "mockprovider":
        raise ValueError("mock_provider_forbidden")
    available = getattr(provider, "is_available", None)
    if callable(available):
        try:
            reachable_value = available()
            reachable = reachable_value if isinstance(reachable_value, bool) else None
        except Exception:
            reachable = False
    else:
        reachable = None
    live_status = None
    if live_report is not None:
        if not isinstance(live_report, dict) or live_report.get("provider") != name:
            raise ValueError("live_report_provider_mismatch")
        if live_report.get("verification_level") != "live":
            raise ValueError("live_report_verification_level_invalid")
        live_status = live_report.get("status")
        if live_status not in {"pass", "fail", "partial"}:
            raise ValueError("live_report_status_invalid")
    capabilities = {"chat": False, "streaming_interface": False,
                    "cooperative_execution_context": False,
                    "streaming_execution_context": False}
    verification = "availability_only"
    detail = "Provider configuration inspected; no inference request was sent."
    try:
        adapter_report = inspect_provider_adapter(
            type("AdapterView", (), {"provider": provider})()
        )
        capabilities.update(adapter_report["capabilities"])
        verification = "structural_only"
    except (TypeError, ValueError):
        detail = "Provider is configured but its adapter contract could not be inspected."
    status = classify_readiness(
        provider_name=name, configured=True, reachable=reachable,
        live_status=live_status,
    )
    if live_status == "pass":
        verification = "live"
        detail = "A matching live conformance report passed."
    elif live_status in {"fail", "partial"}:
        verification = "live"
        detail = "A matching live conformance report did not fully pass."
    elif reachable is True:
        detail = "Provider is reachable; live inference has not been verified."
    elif reachable is False:
        detail = "Configured provider is currently unavailable."
    return ProviderReadiness(
        provider=name, status=status, configured=True, reachable=reachable,
        live_verified=live_status == "pass", capabilities=capabilities,
        verification_level=verification, detail=detail,
    )


__all__ = ["ProviderReadiness", "classify_readiness", "inspect_configured_provider"]
