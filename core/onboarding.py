"""First-run configuration with atomic writes and secret-safe defaults."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

CONFIG_VERSION = 1
DEFAULT_CONFIG_PATH = Path("~/.yuki/config.json").expanduser()
PROVIDERS = {
    "mock": {"kind": "local", "default_model": "mock"},
    "ollama": {"kind": "local", "default_url": "http://127.0.0.1:11434"},
    "llama.cpp": {"kind": "local", "default_url": "http://127.0.0.1:8080/v1"},
    "lm-studio": {"kind": "local", "default_url": "http://127.0.0.1:1234/v1"},
    "openai": {"kind": "cloud", "default_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY"},
    "anthropic": {"kind": "cloud", "default_url": "https://api.anthropic.com", "key_env": "ANTHROPIC_API_KEY"},
    "deepseek": {"kind": "cloud", "default_url": "https://api.deepseek.com/v1", "key_env": "DEEPSEEK_API_KEY"},
    "glm": {"kind": "cloud", "default_url": "https://open.bigmodel.cn/api/paas/v4", "key_env": "ZHIPUAI_API_KEY"},
    "openai-compatible": {"kind": "custom", "default_url": "https://example.invalid/v1", "key_env": "YUKI_CUSTOM_API_KEY"},
}

def config_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path if path is not None else os.environ.get("YUKI_USER_CONFIG", str(DEFAULT_CONFIG_PATH))
    return Path(raw).expanduser().resolve()

def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object.")
    provider = config.get("provider", "mock")
    if provider not in PROVIDERS:
        raise ValueError("Unsupported provider.")
    model = config.get("model", PROVIDERS[provider].get("default_model", ""))
    if not isinstance(model, str) or len(model) > 120:
        raise ValueError("Model must be a string of at most 120 characters.")
    base_url = config.get("base_url", PROVIDERS[provider].get("default_url", ""))
    if not isinstance(base_url, str) or len(base_url) > 2048:
        raise ValueError("base_url must be a bounded string.")
    mcp_servers = config.get("mcp_servers", [])
    if not isinstance(mcp_servers, list) or len(mcp_servers) > 25:
        raise ValueError("mcp_servers must be a list with at most 25 entries.")
    seen = set()
    for server in mcp_servers:
        if not isinstance(server, dict):
            raise ValueError("Each MCP server must be an object.")
        name, endpoint = server.get("name"), server.get("endpoint")
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or name in seen:
            raise ValueError("MCP server names must be unique, non-empty, and bounded.")
        if not isinstance(endpoint, str) or not endpoint.startswith("https://") or len(endpoint) > 2048:
            raise ValueError("MCP endpoints must be HTTPS URLs.")
        tools = server.get("allowed_tools", [])
        if not isinstance(tools, list) or len(tools) > 200 or any(not isinstance(t, str) or not t.strip() for t in tools):
            raise ValueError("MCP allowed_tools must be a bounded list of names.")
        seen.add(name)
    guardrails = config.get("guardrails", {})
    if not isinstance(guardrails, dict):
        raise ValueError("guardrails must be an object.")
    for key in ("require_tool_approval", "require_cloud_approval", "local_first"):
        if key in guardrails and not isinstance(guardrails[key], bool):
            raise ValueError(f"{key} must be boolean.")
    clean = dict(config)
    clean.update({
        "schema_version": CONFIG_VERSION,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key_env": config.get("api_key_env", PROVIDERS[provider].get("key_env", "")),
        "mcp_servers": mcp_servers,
        "guardrails": {
            "require_tool_approval": True,
            "require_cloud_approval": True,
            "local_first": True,
            **guardrails,
        },
    })
    # Credentials must never be serialized into the config document.
    for secret_key in ("api_key", "apiKey", "token", "access_token", "secret"):
        clean.pop(secret_key, None)
    return clean

def save_config(config: dict[str, Any], path: str | os.PathLike[str] | None = None) -> Path:
    target = config_path(path)
    clean = validate_config(config)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    fd, temp_name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=str(target.parent))
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(clean, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return target

def load_user_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any] | None:
    target = config_path(path)
    if not target.exists():
        return None
    if target.stat().st_size > 256_000:
        raise ValueError("Yuki user configuration exceeds 256 KB.")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Yuki user configuration is unreadable or invalid JSON.") from exc
    return validate_config(data)

def onboarding_needed(path: str | os.PathLike[str] | None = None) -> bool:
    return not config_path(path).is_file()
