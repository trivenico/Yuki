"""Small YAML configuration loader with safe environment overrides."""
import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit
import yaml

from core.portable_paths import config_path as resolve_config_path, workspace_path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config.yaml"


def _bounded_int(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise RuntimeError(f"Expected an integer from {minimum} to {maximum}.")
    return value


def _bounded_string(value, key, *, default="", maximum=512, allow_empty=True):
    # Callers supply defaults only for missing keys. Explicit YAML null is invalid.
    if not isinstance(value, str):
        raise RuntimeError(f"{key} must be a string.")
    value = value.strip()
    if (not allow_empty and not value) or len(value) > maximum:
        qualifier = "non-empty " if not allow_empty else ""
        raise RuntimeError(f"{key} must be a {qualifier}string of at most {maximum} characters.")
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


def _string_list(raw, key, *, max_item_length=512):
    value = raw.get(key, [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() or len(item.strip()) > max_item_length
        for item in value
    ):
        raise RuntimeError(
            f"{key} must be a list of non-empty strings no longer than {max_item_length} characters."
        )
    if len(value) > 100:
        raise RuntimeError(f"{key} may contain at most 100 entries.")
    normalized = [item.strip() for item in value]
    if len(set(normalized)) != len(normalized):
        raise RuntimeError(f"{key} must not contain duplicate entries.")
    return normalized


_ALLOWED_CONFIG_KEYS = {
    "name", "workspace", "provider", "ollama_base_url", "ollama_model",
    "max_agent_steps", "voice_enabled", "language_behavior", "voice_style",
    "spoken_reply_policy", "stt_model", "tts_rate", "tts_backend",
    "piper_model_path", "wakeword_enabled", "wakeword_model_path",
    "gmail_enabled", "security_lab_enabled", "recon_allowlisted_targets",
    "sandbox_enabled", "sandbox_allowed_images", "firewall_enabled",
    "firewall_dry_run", "web_allowed_hosts", "plugin_discovery_enabled",
    "trusted_plugin_modules",
}
_SUPPORTED_PROVIDERS = {"mock", "ollama", "hybrid"}
_MAX_CONFIG_BYTES = 128 * 1024


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys instead of silently overriding them."""


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                "unhashable configuration key", key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"duplicate configuration key {key!r}", key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _load_onboarding_json_config(selected_config_path, body):
    """Bridge the onboarding schema to the runtime schema without silently dropping features."""
    import json
    from core.onboarding import validate_config

    try:
        onboarding = validate_config(json.loads(body.decode("utf-8")))
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("Onboarding JSON configuration is invalid.") from exc

    provider = onboarding["provider"]
    if provider not in {"mock", "ollama"}:
        raise RuntimeError(
            f"Provider '{provider}' is configured in onboarding but is not yet supported "
            "by the integrated runtime config bridge. Use mock/ollama or configure the "
            "runtime provider separately; cloud execution is not enabled implicitly."
        )
    if any(server.get("enabled", False) for server in onboarding["mcp_servers"]):
        raise RuntimeError("Enabled MCP servers are not yet wired into the integrated runtime; disable them before startup.")
    if onboarding["daemon"]["enabled"] or onboarding["messaging"]["enabled"]:
        raise RuntimeError("Daemon/messaging onboarding settings are not yet wired into the integrated runtime.")
    security = onboarding["security"]
    if security["allow_shell"] or security["allow_file_write"] or security["allow_network"]:
        raise RuntimeError("Onboarding requested capabilities not enabled by the integrated runtime bridge.")

    # Translate only settings with an explicit, implemented runtime equivalent.
    return {
        "provider": provider,
        "ollama_base_url": onboarding["base_url"] or "http://127.0.0.1:11434",
        "ollama_model": onboarding["model"],
    }


def load_config(path=None):
    selected_config_path = Path(path) if path else resolve_config_path()
    try:
        # Read once and enforce the size limit on the bytes that will actually be parsed.
        body = selected_config_path.read_bytes()
        if len(body) > _MAX_CONFIG_BYTES:
            raise RuntimeError("Configuration file exceeds 128 KiB.")
        if selected_config_path.suffix.lower() == ".json":
            raw = _load_onboarding_json_config(selected_config_path, body)
        else:
            raw = yaml.load(body.decode("utf-8"), Loader=_UniqueKeyLoader) or {}
    except OSError as exc:
        raise RuntimeError(f"Cannot read configuration file: {selected_config_path}") from exc
    except UnicodeError as exc:
        raise RuntimeError("Configuration file must be valid UTF-8.") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError("Configuration YAML is invalid or contains duplicate keys.") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("Configuration must be a YAML mapping.")
    if any(not isinstance(key, str) for key in raw):
        raise RuntimeError("Configuration keys must be strings.")
    unknown_keys = set(raw) - _ALLOWED_CONFIG_KEYS
    if unknown_keys:
        raise RuntimeError("Unknown configuration keys: " + ", ".join(sorted(unknown_keys)))
    # Reject invalid YAML scalar types instead of silently coercing them to strings.
    string_limits = {
        "name": 120, "workspace": 2048, "provider": 32,
        "ollama_base_url": 2048, "ollama_model": 120,
        "language_behavior": 32, "voice_style": 64,
        "spoken_reply_policy": 80, "stt_model": 120,
        "tts_backend": 40, "piper_model_path": 2048,
        "wakeword_model_path": 2048,
    }
    for key, maximum in string_limits.items():
        if key in raw:
            _bounded_string(raw[key], key, maximum=maximum)
    for key in ("voice_enabled", "security_lab_enabled", "sandbox_enabled", "gmail_enabled",
                "firewall_enabled", "firewall_dry_run", "wakeword_enabled", "plugin_discovery_enabled"):
        if key in raw and not isinstance(raw[key], bool):
            raise RuntimeError(f"{key} must be true or false.")
    selected_provider = os.environ.get("YUKI_PROVIDER", str(raw.get("provider", "mock"))).strip().lower()
    if selected_provider not in _SUPPORTED_PROVIDERS:
        raise RuntimeError("provider must be one of: mock, ollama, hybrid.")
    workspace_value = _bounded_string(
        os.environ.get("YUKI_WORKSPACE", raw.get("workspace", "./yuki_workspace")),
        "workspace", maximum=2048, allow_empty=False,
    )
    workspace = Path(workspace_value).expanduser()
    if not workspace.is_absolute(): workspace = workspace_path(str(workspace))
    base_url = _bounded_string(
        os.environ.get("YUKI_OLLAMA_BASE_URL", raw.get("ollama_base_url", "http://127.0.0.1:11434")),
        "ollama_base_url", maximum=2048, allow_empty=False,
    ).rstrip("/")
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
    model = _bounded_string(
        os.environ.get("YUKI_OLLAMA_MODEL", raw.get("ollama_model", "")),
        "ollama_model", maximum=120,
    )
    language_behavior = _bounded_string(
        raw.get("language_behavior", "auto_detect"), "language_behavior", maximum=32, allow_empty=False
    ).lower()
    if language_behavior != "auto_detect":
        raise RuntimeError("language_behavior must be auto_detect.")
    voice_style = _bounded_string(
        raw.get("voice_style", "natural_realistic_female"), "voice_style", maximum=64, allow_empty=False
    ).lower()
    if voice_style != "natural_realistic_female":
        raise RuntimeError("voice_style must be natural_realistic_female.")
    spoken_reply_policy = _bounded_string(
        raw.get("spoken_reply_policy", "every_reply_when_voice_mode_active"),
        "spoken_reply_policy", maximum=80, allow_empty=False,
    ).lower()
    if spoken_reply_policy != "every_reply_when_voice_mode_active":
        raise RuntimeError("spoken_reply_policy is unsupported.")
    display_name = _bounded_string(raw.get("name", "Yuki"), "name", maximum=120, allow_empty=False)
    return {
        "name": display_name, "workspace": str(workspace.resolve()),
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
        "trusted_plugin_modules": _string_list(raw, "trusted_plugin_modules", max_item_length=200),
        "recon_allowlisted_targets": _string_list(raw, "recon_allowlisted_targets", max_item_length=253),
        "sandbox_allowed_images": _string_list(raw, "sandbox_allowed_images", max_item_length=512),
        "web_allowed_hosts": _string_list(raw, "web_allowed_hosts", max_item_length=253),
        "provider": selected_provider,
        "ollama_base_url": base_url, "ollama_model": model,
        "stt_model": _bounded_string(raw.get("stt_model", "small"), "stt_model", maximum=120, allow_empty=False),
        "tts_rate": _bounded_int(raw.get("tts_rate", 170), 80, 260),
        "max_agent_steps": _bounded_int(raw.get("max_agent_steps", 3), 1, 5),
        "tts_backend": _bounded_string(raw.get("tts_backend", "piper"), "tts_backend", maximum=40, allow_empty=False),
        "piper_model_path": _bounded_string(raw.get("piper_model_path", ""), "piper_model_path", maximum=2048),
        "wakeword_model_path": _bounded_string(raw.get("wakeword_model_path", ""), "wakeword_model_path", maximum=2048),
    }
