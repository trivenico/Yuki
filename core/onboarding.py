"""Validated first-run configuration; credentials are environment references only."""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable
from urllib.parse import urlsplit

DEFAULT_CONFIG = Path.home() / ".yuki" / "config.json"
PROVIDERS = {
    "mock", "ollama", "llama_cpp", "lm_studio", "vllm", "openai",
    "anthropic", "deepseek", "glm", "openai_compatible",
}
_SECRET_KEYS = {"api_key", "secret", "password", "access_token", "refresh_token", "token"}


def _check_no_secret_values(value: Any, trail: str = "config") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in _SECRET_KEYS and item not in (None, ""):
                raise ValueError(f"Do not persist secret field {trail}.{key}; use an environment-variable name")
            _check_no_secret_values(item, f"{trail}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_no_secret_values(item, f"{trail}[{index}]")


def _validate_endpoint(value: Any, *, allow_loopback_http: bool, label: str) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError(f"{label} must be a URL shorter than 2048 characters")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        parsed.port  # Validate port syntax/range.
        loopback = host == "localhost"
        if host:
            try:
                loopback = loopback or ipaddress.ip_address(host).is_loopback
            except ValueError:
                pass
        valid_scheme = parsed.scheme == "https" or (
            allow_loopback_http and parsed.scheme == "http" and loopback
        )
        if (not valid_scheme or not host or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError
    except ValueError as exc:
        requirement = "HTTPS (or loopback HTTP for local providers)" if allow_loopback_http else "HTTPS"
        raise ValueError(f"{label} must use {requirement}, with no credentials, query, or fragment") from exc


def validate_config(config: Any) -> dict[str, Any]:
    """Validate loaded or newly-created config identically; reject unknown top-level keys."""
    if not isinstance(config, dict):
        raise ValueError("Config must be an object")
    allowed = {
        "version", "provider", "model", "base_url", "api_key_env", "mcp_servers",
        "security", "daemon", "messaging",
    }
    if set(config) - allowed:
        raise ValueError("Unknown configuration keys: " + ", ".join(sorted(set(config) - allowed)))
    _check_no_secret_values(config)
    if config.get("version", 1) != 1:
        raise ValueError("Unsupported configuration version")
    provider = config.get("provider", "mock")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("Unsupported provider")
    model = config.get("model", "")
    if not isinstance(model, str) or len(model) > 200:
        raise ValueError("model must be a string of at most 200 characters")
    base_url = config.get("base_url", "")
    _validate_endpoint(base_url, allow_loopback_http=provider in {"ollama", "llama_cpp", "lm_studio", "vllm"}, label="base_url")
    key_env = config.get("api_key_env")
    cloud_provider = provider in {"openai", "anthropic", "deepseek", "glm", "openai_compatible"}
    if key_env is not None and (not isinstance(key_env, str) or not key_env.isidentifier()):
        raise ValueError("api_key_env must be an environment variable name")
    if cloud_provider and key_env is None:
        raise ValueError("Cloud providers require api_key_env; the key value itself must remain in the environment")
    mcp_servers = config.get("mcp_servers", [])
    if not isinstance(mcp_servers, list) or len(mcp_servers) > 32:
        raise ValueError("mcp_servers must be a list of at most 32 servers")
    for server in mcp_servers:
        if not isinstance(server, dict) or set(server) - {"endpoint", "enabled", "allowed_tools"}:
            raise ValueError("Each MCP server must contain only endpoint, enabled, and allowed_tools")
        _validate_endpoint(server.get("endpoint"), allow_loopback_http=False, label="MCP endpoint")
        if not isinstance(server.get("enabled", False), bool):
            raise ValueError("MCP enabled must be boolean")
        tools = server.get("allowed_tools", [])
        if not isinstance(tools, list) or len(tools) > 100 or any(not isinstance(x, str) or not x or len(x) > 120 for x in tools):
            raise ValueError("MCP allowed_tools must be a list of at most 100 tool names")
    for section in ("security", "daemon", "messaging"):
        value = config.get(section, {})
        if not isinstance(value, dict):
            raise ValueError(f"{section} must be an object")
    security = config.get("security", {})
    for key in ("allow_network", "allow_file_write", "allow_shell", "require_approval_for_unknown_tools"):
        if key in security and not isinstance(security[key], bool):
            raise ValueError(f"security.{key} must be boolean")
    daemon = config.get("daemon", {})
    if "enabled" in daemon and not isinstance(daemon["enabled"], bool):
        raise ValueError("daemon.enabled must be boolean")
    if "poll_seconds" in daemon and (isinstance(daemon["poll_seconds"], bool) or not isinstance(daemon["poll_seconds"], (int, float)) or not 0.1 <= daemon["poll_seconds"] <= 300):
        raise ValueError("daemon.poll_seconds must be between 0.1 and 300")
    messaging = config.get("messaging", {})
    if "enabled" in messaging and not isinstance(messaging["enabled"], bool):
        raise ValueError("messaging.enabled must be boolean")
    normalized = dict(config)
    normalized.setdefault("version", 1)
    normalized.setdefault("model", "")
    normalized.setdefault("base_url", "")
    normalized.setdefault("api_key_env", None)
    normalized.setdefault("mcp_servers", [])
    normalized.setdefault("security", {"allow_network": False, "allow_file_write": False, "allow_shell": False, "require_approval_for_unknown_tools": True})
    normalized.setdefault("daemon", {"enabled": False, "poll_seconds": 1.0})
    normalized.setdefault("messaging", {"enabled": False})
    return normalized


def save_config(config: dict[str, Any], path: Path = DEFAULT_CONFIG) -> None:
    safe = validate_config(config)
    body = json.dumps(safe, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(body) > 128 * 1024:
        raise ValueError("Config exceeds 128 KiB")
    path = Path(path).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=path.parent)
    try:
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def needs_onboarding(path: Path = DEFAULT_CONFIG) -> bool:
    return not Path(path).expanduser().is_file()


def run_onboarding(
    input_fn: Callable[[str], str] = input,
    *,
    path: Path = DEFAULT_CONFIG,
    validator: Callable[[str, dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    path = Path(path).expanduser()
    if not needs_onboarding(path):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            return validate_config(loaded)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("Existing Yuki config is invalid; it was not overwritten. Back it up and repair it before retrying.") from exc

    print("Yuki first-run setup. API keys are not written to config; set the named environment variable.")
    provider = input_fn("Provider [mock/ollama/llama_cpp/lm_studio/vllm/openai/anthropic/deepseek/glm/openai_compatible] (mock): ").strip().lower() or "mock"
    if provider not in PROVIDERS:
        raise ValueError("Unsupported provider")
    model = input_fn("Model name (optional for mock): ").strip()
    base_url = input_fn("Base URL (blank for provider default): ").strip()
    key_env = None
    if provider in {"openai", "anthropic", "deepseek", "glm", "openai_compatible"}:
        key_env = input_fn("Environment variable name for API key (e.g. OPENAI_API_KEY): ").strip()
        if not key_env.isidentifier():
            raise ValueError("Invalid environment variable name")
    if validator and not validator(provider, {"model": model, "base_url": base_url, "api_key_env": key_env}):
        raise RuntimeError("Provider validation failed")
    # Validate URLs even when no network validator is supplied.
    _validate_endpoint(base_url, allow_loopback_http=provider in {"ollama", "llama_cpp", "lm_studio", "vllm"}, label="base_url")
    mcp_servers = []
    if input_fn("Configure an MCP server? [y/N]: ").strip().lower() == "y":
        endpoint = input_fn("MCP HTTPS endpoint: ").strip()
        _validate_endpoint(endpoint, allow_loopback_http=False, label="MCP endpoint")
        mcp_servers.append({"endpoint": endpoint, "enabled": False, "allowed_tools": []})
    config = {
        "version": 1, "provider": provider, "model": model, "base_url": base_url,
        "api_key_env": key_env, "mcp_servers": mcp_servers,
        "security": {"allow_network": False, "allow_file_write": False, "allow_shell": False, "require_approval_for_unknown_tools": True},
        "daemon": {"enabled": False, "poll_seconds": 1.0}, "messaging": {"enabled": False},
    }
    validated = validate_config(config)
    save_config(validated, path)
    return validated
