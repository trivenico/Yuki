"""Shared model-selection helpers for CLI onboarding and desktop settings."""
from __future__ import annotations
import json
import ipaddress
import os
import tempfile
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import yaml
from core.portable_paths import config_path as resolve_config_path
from core.model_catalog import get_provider, list_providers

def list_installed_ollama_models(base_url: str = "http://127.0.0.1:11434", *, timeout: float = 2.0) -> list[dict[str, str]]:
    """Discover locally installed Ollama models from a loopback server only."""
    if not isinstance(base_url, str) or len(base_url) > 2048:
        raise ValueError("Ollama base URL must be a bounded string.")
    parsed = urlsplit(base_url.rstrip("/"))
    host = parsed.hostname or ""
    loopback = host == "localhost"
    if host:
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Invalid Ollama port.") from exc
    if parsed.scheme != "http" or not loopback or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Model discovery only permits loopback HTTP Ollama endpoints.")
    request = urllib.request.Request(base_url.rstrip("/") + "/api/tags", headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=max(0.5, min(float(timeout), 5.0))) as response:
            raw = response.read(500_001)
    except (urllib.error.URLError, TimeoutError, OSError):
        return []
    if len(raw) > 500_000:
        return []
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    models = payload.get("models") if isinstance(payload, dict) else None
    result = []
    if not isinstance(models, list):
        return result
    seen = set()
    for item in models[:200]:
        if not isinstance(item, dict):
            continue
        model_id = item.get("name")
        if isinstance(model_id, str) and model_id.strip() and len(model_id) <= 120 and model_id not in seen:
            result.append({"id": model_id, "label": model_id, "note": "Installed locally"})
            seen.add(model_id)
    return result

def model_selection_options(kind: str | None = None) -> dict:
    """Return the same catalog shape consumed by onboarding and desktop UI."""
    return {"providers": list_providers(kind), "default_kind": kind or "local"}

def validate_model_selection(selection: dict) -> dict:
    """Normalize a provider/model choice; secrets are never accepted as fields."""
    if not isinstance(selection, dict):
        raise ValueError("Model selection must be an object.")
    allowed = {"provider", "model", "base_url", "api_key_env"}
    if set(selection) - allowed:
        raise ValueError("Model selection contains unsupported fields.")
    provider_id = selection.get("provider")
    meta = get_provider(provider_id)
    model = selection.get("model")
    if not isinstance(model, str) or not model.strip() or len(model.strip()) > 120:
        raise ValueError("Choose a model ID of 1 to 120 characters.")
    base_url = selection.get("base_url", meta["base_url"])
    if not isinstance(base_url, str) or len(base_url) > 2048:
        raise ValueError("Provider endpoint must be a bounded string.")
    key_env = selection.get("api_key_env", meta["key_env"])
    if not isinstance(key_env, str) or (key_env and not key_env.isidentifier()):
        raise ValueError("API key environment variable name is invalid.")
    if meta["kind"] == "cloud":
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Cloud providers must use HTTPS without embedded credentials.")
    if meta["kind"] == "local":
        parsed = urlsplit(base_url)
        host = parsed.hostname or ""
        loopback = host == "localhost"
        if host:
            try:
                loopback = loopback or ipaddress.ip_address(host).is_loopback
            except ValueError:
                pass
        if parsed.scheme == "http" and not loopback:
            raise ValueError("Local HTTP endpoints must bind to loopback.")
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Local endpoint URL is invalid.")
    return {"provider": provider_id, "model": model.strip(), "base_url": base_url.strip(), "api_key_env": key_env}


def apply_model_selection(config: dict, selection: dict) -> dict:
    """Return a new runtime config for a supported selection; never mutate input.

    Catalog entries can describe future providers, but selection cannot enable
    an adapter until the runtime factory implements it.
    """
    validated = validate_model_selection(selection)
    meta = get_provider(validated["provider"])
    if not meta.get("runtime_supported", False):
        raise ValueError("This provider is listed in the catalog but is not yet supported by the runtime.")
    result = dict(config)
    provider = validated["provider"]
    result["provider"] = provider
    if provider == "ollama":
        result["ollama_model"] = validated["model"]
        result["ollama_base_url"] = validated["base_url"].rstrip("/")
    elif provider == "openai":
        result["openai_model"] = validated["model"]
        result["openai_base_url"] = validated["base_url"].rstrip("/")
    else:
        raise ValueError("This provider selection has no runtime configuration mapping.")
    return result



def persist_model_selection(selection: dict, path=None) -> dict:
    """Validate and atomically persist a supported model selection.

    API key values are never accepted or written. Only the environment-variable
    name from the provider catalog is retained in the returned normalized choice.
    """
    validated = validate_model_selection(selection)
    updated = apply_model_selection({}, validated)
    target = Path(path) if path is not None else Path(resolve_config_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = {}
    if target.exists():
        try:
            loaded = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError("Cannot safely read existing Yuki configuration.") from exc
        if not isinstance(loaded, dict):
            raise RuntimeError("Existing Yuki configuration must be a YAML mapping.")
        raw = loaded
    raw.update(updated)
    fd, temporary_name = tempfile.mkstemp(prefix=".yuki-config-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            yaml.safe_dump(raw, stream, allow_unicode=True, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary_name, 0o600)
        except OSError:
            # Windows ACLs govern access; chmod is best-effort there.
            pass
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return {
        "provider": updated["provider"],
        "model": updated.get("ollama_model", updated.get("openai_model", "")),
        "base_url": updated.get("ollama_base_url", updated.get("openai_base_url", "")),
        "api_key_env": validated["api_key_env"],
    }
