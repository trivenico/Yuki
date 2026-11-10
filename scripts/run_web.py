"""Run the Yuki web chat locally.

For public deployments, place this ASGI app behind a TLS-terminating reverse
proxy, configure trusted host/rate-limit policy, and provision secrets securely.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

from api.runtime import build_conversation_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.config import load_config
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol
from providers.agent_adapter import ProviderAgentAdapter
from yuki_providers import ProviderManager


def _load_provider_selection(path: Path):
    """Load a strict, credential-free persisted provider/model selection."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError("Cannot inspect persisted provider selection.") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("Persisted provider selection must be a regular, non-symlink file.")
    if metadata.st_size > 4096:
        raise RuntimeError("Persisted provider selection exceeds the size limit.")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("Cannot read persisted provider selection.") from exc
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("Persisted provider selection is invalid.") from exc
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "provider", "model", "cloud_authorized"}
        or data.get("schema_version") != 2
        or not isinstance(data.get("provider"), str)
        or not isinstance(data.get("model"), str)
        or not data["provider"]
        or type(data.get("cloud_authorized")) is not bool
    ):
        raise RuntimeError("Persisted provider selection has an unsupported schema.")
    return data


def _persist_provider_selection(path: Path, *, provider: str, model: str, cloud_authorized: bool) -> None:
    """Atomically persist provider/model identifiers without credentials."""
    if (
        not isinstance(provider, str) or not provider or len(provider) > 32
        or not isinstance(model, str) or len(model) > 120
        or type(cloud_authorized) is not bool
    ):
        raise ValueError("Invalid provider selection state.")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "schema_version": 2, "provider": provider, "model": model,
        "cloud_authorized": cloud_authorized,
    }, sort_keys=True, separators=(",", ":")) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".provider-selection-", suffix=".tmp", dir=str(path.parent))
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, 0o600)
        else:
            os.chmod(temporary, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise

class DenyAllTools:
    """No tools are exposed by the initial web deployment profile."""

    @staticmethod
    def authorize_tool(name: str) -> bool:
        return False


def create_app():
    config = load_config()
    workspace = Path(config["workspace"]).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)

    auth = BuiltInAuthService(str(workspace / "auth.sqlite3"))
    bootstrap_username = os.environ.get("YUKI_BOOTSTRAP_ADMIN_USERNAME", "").strip()
    bootstrap_password = os.environ.get("YUKI_BOOTSTRAP_ADMIN_PASSWORD", "")
    if bootstrap_username or bootstrap_password:
        if not bootstrap_username or not bootstrap_password:
            raise RuntimeError("Set both YUKI_BOOTSTRAP_ADMIN_USERNAME and YUKI_BOOTSTRAP_ADMIN_PASSWORD.")
        try:
            auth.bootstrap_admin(bootstrap_username, bootstrap_password)
        except PermissionError as exc:
            if str(exc) != "Initial administrator already exists.":
                raise

    # Persist only provider/model identifiers and the explicit cloud-consent bit;
    # never persist credentials. Restored cloud choices require prior consent.
    selection_path = workspace / "provider-selection.json"
    persisted = _load_provider_selection(selection_path)
    if persisted is not None:
        config["provider"] = persisted["provider"]
    provider_manager = ProviderManager(config, environ=os.environ)
    selected_name = str(config.get("provider") or os.environ.get("YUKI_PROVIDER", "openai")).strip().lower()
    cloud_authorized = (
        persisted["cloud_authorized"] if persisted is not None
        else os.environ.get("YUKI_ALLOW_CLOUD", "").strip().lower() == "true"
    )

    catalog_entry = next(
        (item for item in provider_manager.catalog() if item.get("name") == selected_name), None
    )
    if catalog_entry is None:
        raise RuntimeError("The selected provider is not present in the configured catalog.")
    if catalog_entry.get("privacy") != "local_runtime" and not cloud_authorized:
        raise RuntimeError(
            "The selected provider is cloud-hosted. Set YUKI_ALLOW_CLOUD=true for an "
            "explicit initial deployment choice, or choose a local provider."
        )
    configured_model = catalog_entry.get("configured_model") or ""
    if persisted is not None and persisted["model"] != configured_model:
        raise RuntimeError(
            "Persisted provider model no longer matches configuration; review "
            "provider-selection.json before starting Yuki."
        )
    adapter = ProviderAgentAdapter(
        provider_manager.create_selected_runtime_adapter(allow_cloud=cloud_authorized)
    )

    def provider_catalog():
        return provider_manager.catalog()

    def select_provider(name: str, *, allow_cloud: bool = False):
        target = next(
            (item for item in provider_manager.catalog()
             if item.get("name") == name.strip().lower()), None
        )
        if target is None or not target.get("implemented"):
            raise ValueError("provider_not_implemented")
        if target.get("privacy") != "local_runtime" and not allow_cloud:
            raise ValueError("cloud_consent_required")
        previous_name = provider_manager.current().name
        previous_adapter = provider_manager._selected_adapter
        result = provider_manager.select(name, allow_cloud=allow_cloud)
        if result != f"Selected provider: {name.strip().lower()}":
            raise ValueError("provider_not_ready")
        selected_adapter = provider_manager.create_selected_runtime_adapter(allow_cloud=allow_cloud)
        entry = next(
            item for item in provider_manager.catalog()
            if item.get("name") == name.strip().lower()
        )
        try:
            _persist_provider_selection(
                selection_path, provider=name.strip().lower(),
                model=entry.get("configured_model") or "",
                cloud_authorized=allow_cloud,
            )
        except Exception:
            # Restore the router's previous committed state if durable publication
            # fails; the agent adapter has not yet been changed.
            provider_manager._selected = previous_name
            provider_manager._selected_adapter = previous_adapter
            raise
        # Publish the adapter only after exact-model validation and durable state.
        adapter.provider = selected_adapter
        return {
            "name": entry["name"], "label": entry.get("label", entry["name"]),
            "implemented": entry.get("implemented", False), "ready": entry.get("ready", False),
            "privacy": entry.get("privacy", "unknown"),
            "configured_model": entry.get("configured_model"),
            "credential_configured": entry.get("credential_configured", False),
            "selected": True, "status": entry.get("status", "selected"),
        }
    protocol = StructuredToolProtocol(GuardedToolRuntime({}, DenyAllTools()))
    agent_loop = AgentExecutionLoop(
        adapter,
        protocol,
        stream_model=adapter.stream_model,
        max_model_turns=8,
        max_tool_calls=1,
        max_duration_seconds=120,
    )
    return build_conversation_runtime(
        config, auth_service=auth, agent_loop=agent_loop,
        provider_catalog=provider_catalog,
        provider_selector=select_provider,
    )


def main():
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Install the optional web server: python -m pip install -r requirements-web.txt") from exc

    host = os.environ.get("YUKI_WEB_HOST", "127.0.0.1").strip()
    if host not in {"127.0.0.1", "localhost", "::1"} and os.environ.get("YUKI_WEB_ALLOW_PUBLIC_BIND") != "true":
        raise SystemExit("Public binding is disabled. Set YUKI_WEB_ALLOW_PUBLIC_BIND=true only behind a trusted TLS reverse proxy.")
    port_raw = os.environ.get("YUKI_WEB_PORT", "8000")
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise SystemExit("YUKI_WEB_PORT must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("YUKI_WEB_PORT must be between 1 and 65535.")
    uvicorn.run(create_app(), host=host, port=port, lifespan="on")


if __name__ == "__main__":
    main()
