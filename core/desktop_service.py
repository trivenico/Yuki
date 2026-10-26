"""Application service boundary for the Yuki desktop client.

The desktop client may call the configured text provider, but it never invokes
registered skills or shell tools. Secrets are supplied through provider-native
environment/OS configuration and are never accepted by this service API.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core.config import load_config
from core.desktop_onboarding import (
    build_desktop_readiness,
    complete_onboarding_step,
    load_onboarding_state,
)
from yuki_providers import ProviderManager


MAX_PROMPT_CHARS = 8_000
MAX_RESPONSE_CHARS = 12_000
PROVIDER_PROBE_PROMPT = (
    "This is a brief Yuki desktop connectivity check. Reply with exactly: "
    "YUKI_CONNECTION_OK"
)


class DesktopServiceError(RuntimeError):
    """Safe, user-facing desktop operation error."""


class DesktopService:
    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        state_path: str | Path | None = None,
        environ: dict[str, str] | None = None,
        provider_manager: Any | None = None,
    ) -> None:
        self.environ = os.environ if environ is None else environ
        self.config = load_config(config_path)
        self.state_path = Path(state_path) if state_path else (
            Path(self.environ.get("YUKI_HOME", Path.home() / ".yuki"))
            / "desktop_onboarding.json"
        )
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        state = load_onboarding_state(self.state_path)
        if state["selected_provider"]:
            self.config["provider"] = state["selected_provider"]
        self.providers = provider_manager or ProviderManager(self.config, environ=self.environ)
        self._verified_provider: str | None = None

    def list_providers(self) -> list[dict[str, Any]]:
        """Return only provider names and implemented/selected flags."""
        try:
            items = self.providers.list_providers()
        except Exception:
            raise DesktopServiceError("Provider inventory is unavailable.") from None
        result = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name or len(name) > 64:
                continue
            result.append({
                "name": name,
                "implemented": item.get("implemented") is True,
                "selected": item.get("selected") is True,
            })
        return result

    def readiness(self) -> dict[str, Any]:
        return build_desktop_readiness(self.providers, workspace=self.config["workspace"])

    @property
    def provider_verified(self) -> bool:
        """Whether this process has completed a live provider check."""
        return self._verified_provider is not None

    def invalidate_provider_verification(self) -> None:
        """Require a fresh live check after the user changes provider selection."""
        self._verified_provider = None

    def mark_welcome_seen(self) -> dict[str, Any]:
        state = load_onboarding_state(self.state_path)
        if not state["completed_steps"]:
            return complete_onboarding_step(self.state_path, "welcome")
        return state

    def verify_provider(self, name: str) -> dict[str, str]:
        """Select a real adapter and perform a small explicit connectivity request."""
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 64:
            raise DesktopServiceError("Choose a valid provider.")
        key = name.strip().lower()
        known = {item["name"]: item for item in self.list_providers()}
        item = known.get(key)
        if item is None or not item["implemented"]:
            raise DesktopServiceError("That provider is not implemented yet.")
        try:
            selection = self.providers.select(key)
            if not isinstance(selection, str) or not selection.startswith("Selected provider:"):
                raise DesktopServiceError(selection if isinstance(selection, str) else "Provider selection failed.")
            adapter = self.providers.create_selected_adapter()
            if adapter is None or not adapter.is_available():
                self._verified_provider = None
                raise DesktopServiceError(
                    f"{key} is not configured or available. Configure it using the provider's "
                    "supported environment/OS login, then try again. Yuki does not store API keys."
                )
            response = adapter.generate(PROVIDER_PROBE_PROMPT)
            if not isinstance(response, str) or not response.strip():
                self._verified_provider = None
                raise DesktopServiceError("The provider returned an empty connectivity-check response.")
        except DesktopServiceError:
            raise
        except Exception as exc:
            self._verified_provider = None
            # Never echo raw exceptions; HTTP errors can contain URLs or request metadata.
            raise DesktopServiceError(
                f"Provider check failed ({type(exc).__name__}). Check provider setup and retry."
            ) from None
        self._verified_provider = key
        try:
            complete_onboarding_step(
                self.state_path, "provider", selected_provider=key, provider_verified=True
            )
        except ValueError as exc:
            raise DesktopServiceError(str(exc)) from None
        return {"provider": key, "status": "verified", "message": "Provider connection verified."}

    def acknowledge_privacy(self) -> dict[str, Any]:
        state = load_onboarding_state(self.state_path)
        if "provider" not in state["completed_steps"]:
            raise DesktopServiceError("Verify a provider before continuing.")
        return complete_onboarding_step(self.state_path, "privacy")

    def acknowledge_permissions(self) -> dict[str, Any]:
        state = load_onboarding_state(self.state_path)
        if "privacy" not in state["completed_steps"]:
            raise DesktopServiceError("Review privacy information before continuing.")
        return complete_onboarding_step(self.state_path, "permissions")

    def chat(self, prompt: str) -> str:
        """Generate plain text only; this method never dispatches a skill or tool."""
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
            raise DesktopServiceError(f"Message must contain 1–{MAX_PROMPT_CHARS} characters.")
        state = load_onboarding_state(self.state_path)
        if "permissions" not in state["completed_steps"]:
            raise DesktopServiceError("Finish provider setup and review privacy/permissions first.")
        provider_name = self._verified_provider
        if provider_name is None:
            # Do not trust a persisted 'verified' flag as proof of a current live connection.
            raise DesktopServiceError("Verify the provider connection again before chatting.")
        try:
            adapter = self.providers.create_selected_adapter()
            if adapter is None or not adapter.is_available():
                raise DesktopServiceError("Provider is no longer available. Recheck its configuration.")
            answer = adapter.generate(prompt.strip())
        except DesktopServiceError:
            raise
        except Exception as exc:
            raise DesktopServiceError(
                f"Message generation failed ({type(exc).__name__}). Check connection and retry."
            ) from None
        if not isinstance(answer, str) or not answer.strip():
            raise DesktopServiceError("The provider returned an empty response.")
        answer = answer.strip()
        if len(answer) > MAX_RESPONSE_CHARS:
            answer = answer[:MAX_RESPONSE_CHARS] + "\n[Response truncated by Yuki's desktop limit.]"
        state = load_onboarding_state(self.state_path)
        if "first_conversation" not in state["completed_steps"]:
            complete_onboarding_step(self.state_path, "first_conversation")
        return answer
