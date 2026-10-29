"""Safe provider/model selection helpers shared by CLI and desktop onboarding."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from core.model_catalog import get_provider, list_providers
from core.portable_paths import config_path as resolve_config_path

_MODEL_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent loopback model discovery from redirecting to a remote host."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def list_installed_ollama_models(
    base_url: str = "http://127.0.0.1:11434", *, timeout: float = 2.0
) -> list[dict[str, str]]:
    """Discover installed models from a loopback Ollama server only."""
    if not isinstance(base_url, str) or len(base_url) > 2048:
        raise ValueError("Ollama base URL must be a bounded string.")
    try:
        parsed = urlsplit(base_url.rstrip("/"))
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Ollama base URL is invalid.") from exc
    # Require a loopback IP literal. Hostnames (including "localhost") can be
    # remapped by local name-service configuration, so do not resolve names here.
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if (parsed.scheme != "http" or not loopback or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError("Model discovery only permits loopback IP HTTP Ollama endpoints.")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 5:
        raise ValueError("Discovery timeout must be between 0.1 and 5 seconds.")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/tags",
        headers={"Accept": "application/json"}, method="GET",
    )
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
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
    if not isinstance(models, list):
        return []
    result, seen = [], set()
    for item in models[:200]:
        if not isinstance(item, dict):
            continue
        model_id = item.get("name")
        if (isinstance(model_id, str) and model_id.strip() and len(model_id) <= 120
                and not any(ord(ch) < 32 or ord(ch) == 127 for ch in model_id) and model_id.strip() not in seen):
            normalized_id = model_id.strip()
            result.append({"id": normalized_id, "label": normalized_id, "note": "Installed locally"})
            seen.add(normalized_id)
    return result


def model_selection_options(kind: str | None = None, *, supported_only: bool = False) -> dict:
    return {"providers": list_providers(kind, supported_only=supported_only), "default_kind": kind or "local"}


def validate_model_selection(selection: dict) -> dict:
    """Validate a provider/model choice; never accept API key values or arbitrary URLs."""
    if not isinstance(selection, dict):
        raise ValueError("Model selection must be an object.")
    if set(selection) - {"provider", "model"}:
        raise ValueError("Model selection contains unsupported fields.")
    provider_id = selection.get("provider")
    meta = get_provider(provider_id)
    if not meta["runtime_supported"]:
        raise ValueError("This provider is catalogued but not supported by the runtime yet.")
    model = selection.get("model", "")
    if meta["model_config"] is None:
        if model not in ("", None):
            raise ValueError("This provider does not use a selectable model field.")
        normalized_model = ""
    else:
        if not isinstance(model, str):
            raise ValueError("Model ID must be text.")
        normalized_model = model.strip()
        if not _MODEL_ID_PATTERN.fullmatch(normalized_model):
            raise ValueError("Model ID must be 1 to 120 characters and use only letters, numbers, '.', '_', ':', '/', or '-'.")
    return {"provider": provider_id, "model": normalized_model}


def apply_model_selection(config: dict, selection: dict) -> dict:
    """Return a new config with only fields consumed by the real provider factory."""
    if not isinstance(config, dict):
        raise ValueError("Runtime config must be an object.")
    selected = validate_model_selection(selection)
    meta = get_provider(selected["provider"])
    result = dict(config)
    result["provider"] = selected["provider"]
    model_config = meta["model_config"]
    if model_config:
        result[model_config] = selected["model"]
    return result


def persist_model_selection(selection: dict, path=None) -> dict:
    """Atomically save a validated selection without persisting any secret values."""
    selected = validate_model_selection(selection)
    updated = apply_model_selection({}, selected)
    target = Path(path) if path is not None else Path(resolve_config_path())
    raw = {}
    if target.exists():
        try:
            loaded = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise RuntimeError("Cannot safely read existing Yuki configuration.") from exc
        if not isinstance(loaded, dict):
            raise RuntimeError("Existing Yuki configuration must be a YAML mapping.")
        raw = loaded
    raw.update(updated)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".yuki-model-selection-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            yaml.safe_dump(raw, stream, allow_unicode=True, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary_name, 0o600)
        except OSError:
            pass  # Windows ACLs, rather than POSIX mode bits, control access there.
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return selected
