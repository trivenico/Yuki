"""Small YAML configuration loader with safe environment overrides."""
import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
import yaml

from core.portable_paths import config_path as resolve_config_path, workspace_path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config.yaml"

_MCP_HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$")


def _bounded_int(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise RuntimeError(f"Expected an integer from {minimum} to {maximum}.")
    return value


def _bool_setting(env_name, raw, key, default=False):
    value = os.environ.get(env_name)
    if value is not None:
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}: return True
        if normalized in {"0", "false", "no", "off"}: return False
        raise RuntimeError(f"{env_name} must be true or false.")
    value = raw.get(key, default)
    if not isinstance(value, bool): raise RuntimeError(f"{key} must be true or false.")
    return value


def _string_list(raw, key):
    value = raw.get(key, [])
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise RuntimeError(f"{key} must be a list of non-empty strings.")
    if len(value) > 100: raise RuntimeError(f"{key} may contain at most 100 entries.")
    return list(dict.fromkeys(x.strip() for x in value))


def _mcp_servers(raw):
    value = raw.get("mcp_servers", [])
    if not isinstance(value, list) or len(value) > 20:
        raise RuntimeError("mcp_servers must be a list with at most 20 entries.")
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, dict) or set(item) - {"name", "endpoint", "allowed_hosts", "allowed_tools"}:
            raise RuntimeError("Each MCP server must contain only name, endpoint, allowed_hosts, and allowed_tools.")
        name, endpoint = item.get("name"), item.get("endpoint")
        hosts, tools = item.get("allowed_hosts"), item.get("allowed_tools")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name) or name in seen:
            raise RuntimeError("MCP server names must be unique simple identifiers.")
        if (
            not isinstance(endpoint, str)
            or len(endpoint) > 2048
            or endpoint != endpoint.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in endpoint)
        ):
            raise RuntimeError("MCP endpoint must be a bounded HTTPS URL without surrounding whitespace or control characters.")
        if (not isinstance(hosts, list) or not hosts or len(hosts) > 20
                or any(not isinstance(h, str) or not _MCP_HOST_RE.fullmatch(h) for h in hosts)):
            raise RuntimeError("MCP allowed_hosts must be a non-empty list of hostnames.")
        normalized_hosts = list(dict.fromkeys(host.lower() for host in hosts))
        try:
            parsed = urlsplit(endpoint)
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError("MCP endpoint must contain a valid port.") from exc
        if port == 0:
            raise RuntimeError("MCP endpoint port must be between 1 and 65535.")
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise RuntimeError("MCP endpoint must be HTTPS and must not contain credentials or a fragment.")
        if parsed.hostname.lower() not in normalized_hosts:
            raise RuntimeError("MCP endpoint hostname must appear in allowed_hosts.")
        if (not isinstance(tools, list) or len(tools) > 100
                or any(not isinstance(t, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", t) for t in tools)):
            raise RuntimeError("MCP allowed_tools must be a list of simple tool names.")
        result.append({"name": name, "endpoint": endpoint, "allowed_hosts": normalized_hosts,
                       "allowed_tools": list(dict.fromkeys(tools))})
        seen.add(name)
    return result


def _optional_model_setting(raw, key, default=""):
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise RuntimeError(f"{key} must be a valid model identifier string.")
    value = value.strip()
    if len(value) > 120 or (value and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", value)):
        raise RuntimeError(f"{key} must be a valid model identifier of at most 120 characters.")
    return value


def load_config(path=None):
    selected_config_path = Path(path) if path else resolve_config_path()
    try:
        raw = yaml.safe_load(selected_config_path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise RuntimeError(f"Cannot read configuration file: {selected_config_path}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError("Configuration YAML is invalid.") from exc
    if not isinstance(raw, dict): raise RuntimeError("Configuration must be a YAML mapping.")
    workspace_value = os.environ.get("YUKI_WORKSPACE", raw.get("workspace", "./yuki_workspace"))
    if not isinstance(workspace_value, str) or not workspace_value.strip():
        raise RuntimeError("workspace must be a non-empty path.")
    workspace = Path(workspace_value).expanduser()
    if not workspace.is_absolute(): workspace = workspace_path(str(workspace))
    base_url = os.environ.get("YUKI_OLLAMA_BASE_URL", str(raw.get("ollama_base_url", "http://127.0.0.1:11434"))).strip().rstrip("/")
    try:
        parsed_url = urlsplit(base_url)
        hostname = parsed_url.hostname
        # Accessing .port validates malformed/out-of-range port values.
        parsed_url.port
        is_loopback_name = hostname == "localhost"
        if hostname:
            try:
                is_loopback_name = is_loopback_name or ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                pass
        safe_transport = parsed_url.scheme == "https" or (
            parsed_url.scheme == "http" and is_loopback_name
        )
        if (not safe_transport or not hostname or parsed_url.username or parsed_url.password
                or parsed_url.query or parsed_url.fragment):
            raise ValueError("unsafe URL")
    except ValueError as exc:
        raise RuntimeError("Ollama URL must be HTTPS or loopback HTTP, with no credentials, query, or fragment.") from exc
    github_roots_raw = os.environ.get("YUKI_GITHUB_ALLOWED_ROOTS")
    if github_roots_raw is not None:
        github_roots = [item.strip() for item in github_roots_raw.split(os.pathsep) if item.strip()]
        if len(github_roots) > 50:
            raise RuntimeError("YUKI_GITHUB_ALLOWED_ROOTS may contain at most 50 paths.")
    else:
        github_roots = _string_list(raw, "github_allowed_roots")
    model = os.environ.get("YUKI_OLLAMA_MODEL", str(raw.get("ollama_model", "llama3.2:3b"))).strip()
    if len(model) > 120: raise RuntimeError("ollama_model is too long.")
    language_behavior = str(raw.get("language_behavior", "auto_detect")).strip().lower()
    if language_behavior != "auto_detect": raise RuntimeError("language_behavior must be auto_detect.")
    voice_style = str(raw.get("voice_style", "natural_realistic_female")).strip().lower()
    if voice_style != "natural_realistic_female": raise RuntimeError("voice_style must be natural_realistic_female.")
    spoken_reply_policy = str(raw.get("spoken_reply_policy", "every_reply_when_voice_mode_active")).strip().lower()
    if spoken_reply_policy != "every_reply_when_voice_mode_active": raise RuntimeError("spoken_reply_policy is unsupported.")
    return {
        "name": "Yuki", "workspace": str(workspace.resolve()),
        "language_behavior": language_behavior, "voice_style": voice_style,
        "spoken_reply_policy": spoken_reply_policy,
        "voice_enabled": _bool_setting("YUKI_VOICE_ENABLED", raw, "voice_enabled", False),
        "security_lab_enabled": _bool_setting("YUKI_SECURITY_LAB_ENABLED", raw, "security_lab_enabled", False),
        "sandbox_enabled": _bool_setting("YUKI_SANDBOX_ENABLED", raw, "sandbox_enabled", False),
        "gmail_enabled": _bool_setting("YUKI_GMAIL_ENABLED", raw, "gmail_enabled", False),
        "firewall_enabled": _bool_setting("YUKI_FIREWALL_ENABLED", raw, "firewall_enabled", False),
        "firewall_dry_run": _bool_setting("YUKI_FIREWALL_DRY_RUN", raw, "firewall_dry_run", True),
        "wakeword_enabled": _bool_setting("YUKI_WAKEWORD_ENABLED", raw, "wakeword_enabled", False),
        "plugin_discovery_enabled": _bool_setting("YUKI_PLUGIN_DISCOVERY_ENABLED", raw, "plugin_discovery_enabled", False),
        "background_evolution_enabled": _bool_setting("YUKI_BACKGROUND_EVOLUTION_ENABLED", raw, "background_evolution_enabled", False),
        "agent_council_enabled": _bool_setting("YUKI_AGENT_COUNCIL_ENABLED", raw, "agent_council_enabled", True),
        "screen_observation_enabled": _bool_setting("YUKI_SCREEN_OBSERVATION_ENABLED", raw, "screen_observation_enabled", False),
        "camera_access_enabled": _bool_setting("YUKI_CAMERA_ACCESS_ENABLED", raw, "camera_access_enabled", False),
        "desktop_control_enabled": _bool_setting("YUKI_DESKTOP_CONTROL_ENABLED", raw, "desktop_control_enabled", False),
        "trusted_plugin_modules": _string_list(raw, "trusted_plugin_modules"),
        "github_allowed_roots": [str(Path(p).expanduser().resolve()) for p in github_roots],
        "mcp_servers": _mcp_servers(raw),
        "recon_allowlisted_targets": _string_list(raw, "recon_allowlisted_targets"),
        "sandbox_allowed_images": _string_list(raw, "sandbox_allowed_images"),
        "web_allowed_hosts": _string_list(raw, "web_allowed_hosts"),
        "provider": os.environ.get("YUKI_PROVIDER", str(raw.get("provider", "ollama"))).strip().lower(),
        "ollama_base_url": base_url, "ollama_model": model,
        "openai_model": _optional_model_setting(raw, "openai_model", "gpt-4.1-mini"),
        "deepseek_model": _optional_model_setting(raw, "deepseek_model"),
        "kimi_model": _optional_model_setting(raw, "kimi_model"),
        "minimax_model": _optional_model_setting(raw, "minimax_model"),
        "openrouter_model": _optional_model_setting(raw, "openrouter_model"),
        "tokenrouter_model": _optional_model_setting(raw, "tokenrouter_model"),
        "anthropic_model": _optional_model_setting(raw, "anthropic_model"),
        "stt_model": str(raw.get("stt_model", "small")),
        "tts_rate": _bounded_int(raw.get("tts_rate", 170), 80, 260),
        "max_agent_steps": _bounded_int(raw.get("max_agent_steps", 3), 1, 5),
        "tts_backend": str(raw.get("tts_backend", "piper")),
        "piper_model_path": str(raw.get("piper_model_path", "")),
        "wakeword_model_path": str(raw.get("wakeword_model_path", "")),
    }
