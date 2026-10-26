"""Safe, resumable first-run onboarding primitives for desktop clients.

This module stores progress only. It must never store provider credentials,
conversation text, or skill arguments. Provider secrets belong in OS secret
storage integration provided by the desktop shell.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
ONBOARDING_STEPS = ("welcome", "provider", "privacy", "permissions", "first_conversation")
_ALLOWED_STEPS = frozenset(ONBOARDING_STEPS)
_MAX_STATE_BYTES = 16 * 1024


def _empty_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "completed_steps": [],
            "selected_provider": None, "completed": False}


def _validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return _empty_state()
    completed, provider, done = (
        value.get("completed_steps"), value.get("selected_provider"), value.get("completed")
    )
    if (not isinstance(completed, list) or len(completed) > len(ONBOARDING_STEPS)
            or any(not isinstance(s, str) or s not in _ALLOWED_STEPS for s in completed)
            or len(set(completed)) != len(completed) or not isinstance(done, bool)
            or (provider is not None and (
                not isinstance(provider, str) or not provider or len(provider) > 64
            ))):
        return _empty_state()
    # Persisted progress must be a contiguous prefix. Never let a corrupt or
    # manually edited file skip a consent/setup gate.
    normalized = []
    for expected in ONBOARDING_STEPS:
        if expected not in completed:
            break
        normalized.append(expected)
    if "provider" not in normalized:
        provider = None
    elif provider is None or not provider.strip():
        # Provider setup cannot be considered complete without a selected,
        # validated provider. Drop this step and every later step.
        normalized = normalized[:normalized.index("provider")]
        provider = None
    else:
        provider = provider.strip().lower()
    return {"schema_version": SCHEMA_VERSION, "completed_steps": normalized,
            "selected_provider": provider, "completed": normalized == list(ONBOARDING_STEPS)}


def load_onboarding_state(path: str | Path) -> dict[str, Any]:
    """Load progress defensively; corrupt, oversized or absent files start fresh."""
    target = Path(path)
    try:
        if target.stat().st_size > _MAX_STATE_BYTES:
            return _empty_state()
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _empty_state()
    return _validate_state(raw)


def save_onboarding_state(path: str | Path, state: dict[str, Any]) -> dict[str, Any]:
    """Atomically persist validated progress without accepting arbitrary fields."""
    normalized = _validate_state(state)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".onboarding-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(normalized, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return dict(normalized)


def complete_onboarding_step(path: str | Path, step: str, *,
                             selected_provider: str | None = None,
                             provider_verified: bool = False) -> dict[str, Any]:
    """Record an ordered step; provider completion requires a successful live check.

    The caller must perform a real provider connectivity/model check before setting
    provider_verified=True. This module deliberately never receives credentials.
    """
    if not isinstance(step, str) or step not in _ALLOWED_STEPS:
        raise ValueError("Unknown onboarding step.")
    state = load_onboarding_state(path)
    completed = state["completed_steps"]
    index = ONBOARDING_STEPS.index(step)
    if step not in completed:
        if index != len(completed):
            raise ValueError("Complete onboarding steps in order.")
        completed.append(step)
    if step == "provider":
        if (not isinstance(selected_provider, str) or not selected_provider.strip()
                or len(selected_provider.strip()) > 64):
            raise ValueError("A configured provider name is required for this step.")
        if provider_verified is not True:
            raise ValueError("Verify provider connectivity before completing this step.")
        normalized_provider = selected_provider.strip().lower()
        previous_provider = state["selected_provider"]
        if previous_provider is not None and previous_provider != normalized_provider:
            # Changing the provider invalidates all later onboarding claims,
            # including the first-conversation smoke test.
            completed[:] = completed[:index]
            completed.append(step)
        state["selected_provider"] = normalized_provider
    elif selected_provider is not None or provider_verified is not False:
        raise ValueError("Provider verification is only accepted on the provider step.")
    state["completed"] = completed == list(ONBOARDING_STEPS)
    return save_onboarding_state(path, state)


def reset_onboarding(path: str | Path) -> dict[str, Any]:
    """Reset progress only; never deletes credentials or user data."""
    return save_onboarding_state(path, _empty_state())


def build_desktop_readiness(provider_manager: Any, *, workspace: str | Path) -> dict[str, Any]:
    """Return UI-safe readiness metadata; never include keys, env values, or exceptions."""
    try:
        providers = provider_manager.list_providers()
    except Exception:
        providers = []
    safe_providers = []
    for item in providers if isinstance(providers, list) else []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name or len(name) > 64:
            continue
        safe_providers.append({"name": name, "implemented": item.get("implemented") is True,
                               "selected": item.get("selected") is True})
    selected = next((p["name"] for p in safe_providers if p["selected"]), None)
    try:
        folder = Path(workspace).expanduser().resolve()
        exists = folder.is_dir()
        writable = exists and os.access(folder, os.W_OK)
    except (OSError, TypeError, ValueError):
        exists, writable = False, False
    return {"schema_version": SCHEMA_VERSION, "provider_selected": selected is not None,
            "selected_provider": selected, "providers": safe_providers,
            "workspace": {"exists": exists, "writable": writable},
            "credentials_included": False}
