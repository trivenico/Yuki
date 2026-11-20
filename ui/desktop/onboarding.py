"""Resumable, fail-closed first-run onboarding state for Yuki Desktop.

Only progress metadata is persisted here. Credentials, secrets, conversation
content, and permission grants remain owned by their respective services.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from core.json_file_store import atomic_write_json, file_lock


class OnboardingStateError(RuntimeError):
    """Raised when onboarding state is invalid or cannot be safely persisted."""


class DesktopOnboarding:
    """Persist a strict, sequential onboarding checklist in a private profile."""

    SCHEMA_VERSION = 1
    STEPS = (
        "welcome",
        "readiness",
        "provider",
        "privacy",
        "permissions",
        "first_conversation",
    )
    PROVIDERS = frozenset({"native", "openai", "ollama", "gemini", "hybrid", "defer"})

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        if not str(self.path).strip():
            raise ValueError("onboarding state path must be non-empty.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._validate_file_path()
        with file_lock(self.path):
            if not self.path.exists():
                atomic_write_json(self.path, self._empty_state())
            else:
                self._read_unlocked()

    @classmethod
    def _empty_state(cls) -> dict[str, Any]:
        return {
            "schema_version": cls.SCHEMA_VERSION,
            "completed_steps": [],
            "provider_choice": None,
        }

    def _validate_file_path(self) -> None:
        if self.path.is_symlink():
            raise OnboardingStateError("onboarding state must not be a symbolic link.")
        if self.path.exists():
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OnboardingStateError(
                    "onboarding state must be a regular single-link file."
                )
            if os.name == "posix":
                os.chmod(self.path, 0o600)
                if stat.S_IMODE(self.path.stat().st_mode) != 0o600:
                    raise OnboardingStateError("onboarding state permissions are not private.")

    @classmethod
    def _validate_state(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {
            "schema_version", "completed_steps", "provider_choice"
        }:
            raise OnboardingStateError("onboarding state has an unsupported schema.")
        if type(value["schema_version"]) is not int or value["schema_version"] != cls.SCHEMA_VERSION:
            raise OnboardingStateError("onboarding state version is unsupported.")
        completed = value["completed_steps"]
        if not isinstance(completed, list) or len(completed) > len(cls.STEPS):
            raise OnboardingStateError("onboarding progress is invalid.")
        if completed != list(cls.STEPS[:len(completed)]):
            raise OnboardingStateError("onboarding progress is not a valid sequential prefix.")
        provider = value["provider_choice"]
        if provider is not None and (not isinstance(provider, str) or provider not in cls.PROVIDERS):
            raise OnboardingStateError("onboarding provider choice is invalid.")
        provider_step_done = "provider" in completed
        if provider_step_done != (provider is not None):
            raise OnboardingStateError("onboarding provider choice does not match progress.")
        return {
            "schema_version": cls.SCHEMA_VERSION,
            "completed_steps": list(completed),
            "provider_choice": provider,
        }

    def _read_unlocked(self) -> dict[str, Any]:
        self._validate_file_path()
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OnboardingStateError(
                "onboarding state cannot be read safely; it was not reset."
            ) from exc
        return self._validate_state(value)

    def _write_unlocked(self, state: dict[str, Any]) -> None:
        self._validate_file_path()
        atomic_write_json(self.path, self._validate_state(state))

    def snapshot(self) -> dict[str, Any]:
        """Return a validated copy of the current persisted progress."""
        with file_lock(self.path):
            return self._read_unlocked()

    @property
    def current_step(self) -> str | None:
        state = self.snapshot()
        index = len(state["completed_steps"])
        return self.STEPS[index] if index < len(self.STEPS) else None

    @property
    def is_complete(self) -> bool:
        return self.current_step is None

    def complete_step(
        self, step: str, *, accepted: bool, provider: str | None = None,
    ) -> dict[str, Any]:
        """Complete exactly the current step after an explicit positive action.

        Readiness means the user reviewed the diagnostic results; optional
        capabilities may remain unavailable. Provider credentials are never
        accepted or stored by this state machine.
        """
        if step not in self.STEPS:
            raise ValueError("unknown onboarding step.")
        if accepted is not True:
            raise ValueError("onboarding steps require explicit acceptance.")
        if step == "provider":
            if provider not in self.PROVIDERS:
                raise ValueError("choose a supported provider or defer configuration.")
        elif provider is not None:
            raise ValueError("provider may only be supplied for the provider step.")

        with file_lock(self.path):
            state = self._read_unlocked()
            index = len(state["completed_steps"])
            if index >= len(self.STEPS) or self.STEPS[index] != step:
                raise OnboardingStateError("onboarding steps must be completed in order.")
            state["completed_steps"].append(step)
            if step == "provider":
                state["provider_choice"] = provider
            self._write_unlocked(state)
            return state

    def reset(self, *, confirmed: bool = False) -> dict[str, Any]:
        """Explicitly restart onboarding without changing account credentials."""
        if confirmed is not True:
            raise ValueError("reset requires explicit confirmation.")
        with file_lock(self.path):
            state = self._empty_state()
            self._write_unlocked(state)
            return state


__all__ = ["DesktopOnboarding", "OnboardingStateError"]
