"""Wire the structured evolution reviewer to Yuki's configured provider factory.

This is an opt-in integration helper. It does not grant approval, execute
tools, or turn an AI reviewer into an authenticated human reviewer.
"""
from __future__ import annotations

from typing import Mapping

from automation.model_review_adapter import ModelReviewAdapter


class ConfiguredModelReviewerError(RuntimeError):
    """Configured provider cannot safely serve as a reviewer."""


def create_configured_model_reviewer(
    *,
    reviewer_id: str,
    config: Mapping | None = None,
    environ: Mapping[str, str] | None = None,
) -> ModelReviewAdapter:
    """Build a reviewer from the existing Yuki provider factory.

    The caller must explicitly configure the provider and reviewer identity.
    Test-only mock providers are rejected. Provider readiness is checked
    before returning the adapter; there is no silent provider fallback.
    """
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise ValueError("reviewer_id must be explicitly configured.")
    if config is not None and not isinstance(config, Mapping):
        raise TypeError("config must be a mapping.")
    if environ is not None and not isinstance(environ, Mapping):
        raise TypeError("environ must be a mapping.")

    try:
        from providers.factory import create_provider
        from providers.mock import MockProvider

        provider = create_provider(config=config, environ=environ)
    except Exception as exc:
        raise ConfiguredModelReviewerError("configured model provider could not be constructed.") from exc

    if isinstance(provider, MockProvider):
        raise ConfiguredModelReviewerError("mock provider is test-only and cannot review evolution changes.")
    available = getattr(provider, "is_available", None)
    if not callable(available):
        raise ConfiguredModelReviewerError("configured provider does not expose readiness checks.")
    try:
        ready = available()
    except Exception as exc:
        raise ConfiguredModelReviewerError("configured provider readiness check failed.") from exc
    if ready is not True:
        raise ConfiguredModelReviewerError("configured provider is not ready; no fallback was attempted.")

    try:
        return ModelReviewAdapter(provider, reviewer_id=reviewer_id.strip())
    except (TypeError, ValueError) as exc:
        raise ConfiguredModelReviewerError("configured reviewer identity or provider is invalid.") from exc
