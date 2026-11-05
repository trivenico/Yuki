"""Admin-controlled, persistent provider selection for a single Yuki runtime.

Selection is process-wide (not per-user). Only administrators can change it.
Only implemented providers whose local prerequisites are ready are selectable.
The selection file contains a provider identifier only, never credentials.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from providers.catalog import build_provider_catalog
from providers.factory import create_provider
from providers.provider_readiness import inspect_configured_provider
from providers.provider_policy import is_mock_provider


class ProviderRuntimeManager:
    """Safely switch the shared provider adapter and persist the selected ID."""

    def __init__(
        self,
        config: dict[str, Any],
        adapter: Any,
        state_path: str | Path,
        *,
        environ: Mapping[str, str] | None = None,
        provider_factory: Callable[..., Any] = create_provider,
        catalog_factory: Callable[..., list[dict[str, Any]]] = build_provider_catalog,
    ):
        if not isinstance(config, dict):
            raise TypeError("config must be a dictionary")
        if adapter is None or not hasattr(adapter, "provider"):
            raise TypeError("adapter must expose a provider attribute")
        if not callable(provider_factory) or not callable(catalog_factory):
            raise TypeError("provider and catalog factories must be callable")
        self.config = config
        self.adapter = adapter
        self.state_path = Path(state_path).expanduser()
        self.environ = os.environ if environ is None else environ
        self.provider_factory = provider_factory
        self.catalog_factory = catalog_factory
        self._lock = threading.RLock()
        self._load_persisted_selection()

    def catalog(self) -> list[dict[str, Any]]:
        with self._lock:
            return self.catalog_factory(config=self.config, environ=self.environ)

    def readiness(self, *, live_report: dict[str, Any] | None = None):
        """Inspect the active provider without prompting or silently switching it."""
        with self._lock:
            name = str(self.config.get("provider", "")).strip().lower()
            if not name or name == "mock":
                raise RuntimeError("real_provider_selection_required")
            if is_mock_provider(self.adapter.provider):
                raise RuntimeError("mock_provider_forbidden")
            # Readiness must describe the same implementation that will receive
            # requests. Provider constructors must not perform network calls.
            expected = self.provider_factory(dict(self.config), environ=self.environ)
            if is_mock_provider(expected) or type(expected) is not type(self.adapter.provider):
                raise RuntimeError("active_provider_configuration_mismatch")
            return inspect_configured_provider(
                config=self.config,
                provider_factory=lambda _config: self.adapter.provider,
                live_report=live_report,
            )

    def select(self, provider_name: str) -> dict[str, Any]:
        if not isinstance(provider_name, str) or len(provider_name) > 32:
            raise ValueError("invalid_provider")
        name = provider_name.strip().lower()
        with self._lock:
            entries = self.catalog_factory(config=self.config, environ=self.environ)
            selected = next((item for item in entries if item.get("name") == name), None)
            if selected is None or not selected.get("implemented"):
                raise ValueError("provider_not_implemented")
            if not selected.get("ready"):
                raise ValueError("provider_not_ready")
            candidate_config = dict(self.config)
            candidate_config["provider"] = name
            # Construct before changing either active state or persisted state.
            candidate = self.provider_factory(candidate_config, environ=self.environ)
            if candidate is None or is_mock_provider(candidate):
                raise ValueError("mock_provider_forbidden")
            self._persist(name)
            self.adapter.provider = candidate
            self.config["provider"] = name
            return next(
                item for item in self.catalog_factory(config=self.config, environ=self.environ)
                if item.get("name") == name
            )

    def _load_persisted_selection(self) -> None:
        try:
            raw = self.state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RuntimeError("Cannot read persisted provider selection.") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Persisted provider selection is invalid.") from exc
        if not isinstance(data, dict) or set(data) != {"schema_version", "provider"}:
            raise RuntimeError("Persisted provider selection has an unsupported schema.")
        if data["schema_version"] != 1 or not isinstance(data["provider"], str):
            raise RuntimeError("Persisted provider selection has an unsupported schema.")
        name = data["provider"]
        entries = self.catalog_factory(config=self.config, environ=self.environ)
        selected = next((item for item in entries if item.get("name") == name), None)
        if selected is None or not selected.get("implemented") or not selected.get("ready"):
            raise RuntimeError("Persisted provider is no longer configured and ready; repair provider credentials or remove provider-selection.json.")
        candidate_config = dict(self.config)
        candidate_config["provider"] = name
        try:
            candidate = self.provider_factory(candidate_config, environ=self.environ)
        except Exception as exc:
            raise RuntimeError("Cannot initialize the persisted Yuki provider selection.") from exc
        if is_mock_provider(candidate):
            raise RuntimeError("Persisted provider selection resolved to a forbidden mock provider.")
        self.adapter.provider = candidate
        self.config["provider"] = name

    def _persist(self, name: str) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"schema_version": 1, "provider": name},
            sort_keys=True, separators=(",", ":"),
        ) + "\n"
        fd, temporary = tempfile.mkstemp(
            prefix=".provider-selection-", suffix=".tmp", dir=str(self.state_path.parent)
        )
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            else:
                os.chmod(temporary, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
            try:
                directory_fd = os.open(self.state_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                # Some platforms/filesystems do not support directory fsync.
                pass
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
