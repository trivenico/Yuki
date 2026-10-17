"""Runtime health checks for Yuki's local dependencies.

Checks are read-only and bounded. This module deliberately does not restart services,
modify firewall rules, or perform recovery actions; those require separately reviewed
handlers and explicit approval.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from core.self_healing import HealthResult, HealthStatus, SelfHealingManager


def _workspace_writable(path: Path) -> bool:
    return path.is_dir() and os.access(path, os.R_OK | os.W_OK | os.X_OK)


def _ollama_ready(base_url: str, model: str) -> bool:
    # Only allow local Ollama endpoints from this diagnostic check. This avoids
    # turning a health command into an arbitrary network request.
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return False
    if not model.strip():
        return False
    request = Request(base_url.rstrip("/") + "/api/tags", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=1.5) as response:
            if response.status != 200:
                return False
            import json
            payload = json.loads(response.read(1_000_000).decode("utf-8"))
        models = payload.get("models", []) if isinstance(payload, dict) else []
        names = {
            item.get("name")
            for item in models
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        return model in names or any(name.split(":")[0] == model.split(":")[0] for name in names)
    except (OSError, ValueError, URLError, TimeoutError):
        return False


def build_runtime_health_manager(config: dict, workspace: Path) -> SelfHealingManager:
    """Create safe, read-only health checks for the configured runtime."""
    manager = SelfHealingManager()
    manager.register_check("workspace", lambda: _workspace_writable(workspace))

    provider = str(config.get("provider", "mock")).strip().lower()
    manager.register_check(
        "provider_configuration",
        lambda: provider in {"mock", "ollama", "openai", "anthropic", "hybrid"},
    )

    if provider in {"ollama", "hybrid"}:
        manager.register_check(
            "ollama_local_model",
            lambda: _ollama_ready(
                str(config.get("ollama_base_url", "http://127.0.0.1:11434")),
                str(config.get("ollama_model", "")),
            ),
        )

    if config.get("voice_enabled"):
        backend = str(config.get("tts_backend", "piper")).strip().lower()
        model_path = str(config.get("piper_model_path", "")).strip()
        if backend == "piper":
            manager.register_check(
                "piper_voice_model",
                lambda: bool(model_path) and Path(model_path).expanduser().is_file(),
            )

    discovery_enabled = bool(config.get("plugin_discovery_enabled", False))
    trusted_modules = config.get("trusted_plugin_modules", [])
    manager.register_check(
        "plugin_trust_policy",
        lambda: (
            isinstance(trusted_modules, list)
            and all(isinstance(item, str) and item.strip() and item != "*" for item in trusted_modules)
            and (not discovery_enabled or bool(trusted_modules))
        ),
    )
    return manager


def summarize_health(manager: SelfHealingManager) -> list[tuple[str, str, str]]:
    """Return status/message tuples without exposing exception text or secrets."""
    rows = []
    for result in manager.check_all():
        message = result.message or {
            HealthStatus.HEALTHY: "Check passed.",
            HealthStatus.UNHEALTHY: "Check failed.",
            HealthStatus.DEGRADED: "Check reports degraded service.",
            HealthStatus.UNKNOWN: "Check result is unknown.",
        }[result.status]
        rows.append((result.name, result.status.value, message))
    return rows
