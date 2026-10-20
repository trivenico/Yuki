"""Shared typed contract and normalization helpers for Yuki onboarding configuration.

Runtime YAML still has a legacy schema; core.config bridges supported onboarding
settings into that runtime and rejects settings it cannot honor.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, TypedDict


class SecurityConfig(TypedDict):
    allow_network: bool
    allow_file_write: bool
    allow_shell: bool
    require_approval_for_unknown_tools: bool


class DaemonConfig(TypedDict):
    enabled: bool
    poll_seconds: float


class MessagingConfig(TypedDict):
    enabled: bool


class MCPServerConfig(TypedDict):
    endpoint: str
    enabled: bool
    allowed_tools: list[str]


class OnboardingConfig(TypedDict):
    version: int
    provider: str
    model: str
    base_url: str
    api_key_env: str | None
    mcp_servers: list[MCPServerConfig]
    security: SecurityConfig
    daemon: DaemonConfig
    messaging: MessagingConfig


ONBOARDING_CONFIG_VERSION = 1

SECTION_DEFAULTS: dict[str, dict[str, Any]] = {
    "security": {
        "allow_network": False,
        "allow_file_write": False,
        "allow_shell": False,
        "require_approval_for_unknown_tools": True,
    },
    "daemon": {"enabled": False, "poll_seconds": 1.0},
    "messaging": {"enabled": False},
}


def normalize_onboarding_defaults(config: dict[str, Any]) -> dict[str, Any]:
    """Return an isolated onboarding config with all documented defaults filled."""
    normalized = deepcopy(config)
    normalized.setdefault("version", ONBOARDING_CONFIG_VERSION)
    normalized.setdefault("provider", "mock")
    normalized.setdefault("model", "")
    normalized.setdefault("base_url", "")
    normalized.setdefault("api_key_env", None)
    normalized.setdefault("mcp_servers", [])
    for section, defaults in SECTION_DEFAULTS.items():
        supplied = normalized.get(section, {})
        # Validation of section/key types is performed before this function is called.
        normalized[section] = {**defaults, **supplied}
    return normalized



def onboarding_to_runtime_config(config: dict[str, Any]) -> dict[str, str]:
    """Translate only onboarding settings supported by the current runtime.

    The caller must validate the onboarding object first. Unsupported capabilities
    fail closed so config cannot imply that disabled runtime features are active.
    """
    provider = config["provider"]
    if provider not in {"mock", "ollama"}:
        raise ValueError(
            f"Provider '{provider}' is configured in onboarding but is not yet supported "
            "by the integrated runtime config bridge. Use mock/ollama or configure the "
            "runtime provider separately; cloud execution is not enabled implicitly."
        )
    if any(server.get("enabled", False) for server in config["mcp_servers"]):
        raise ValueError(
            "Enabled MCP servers are not yet wired into the integrated runtime; disable them before startup."
        )
    if config["daemon"]["enabled"] or config["messaging"]["enabled"]:
        raise ValueError("Daemon/messaging onboarding settings are not yet wired into the integrated runtime.")
    security = config["security"]
    if security["allow_shell"] or security["allow_file_write"] or security["allow_network"]:
        raise ValueError("Onboarding requested capabilities not enabled by the integrated runtime bridge.")
    return {
        "provider": provider,
        "ollama_base_url": config["base_url"] or "http://127.0.0.1:11434",
        "ollama_model": config["model"],
    }
