"""Versioned prompt loading from YAML with safe fallback to the built-in persona."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib


@dataclass(frozen=True)
class PromptProfile:
    name: str
    version: str
    system_guidance: str
    experiment: str = "control"


class PromptManager:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else Path(__file__).with_name("persona.yaml")

    def load(self) -> PromptProfile:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is required to load external prompt profiles.") from exc
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("Prompt profile could not be loaded.") from exc
        if not isinstance(raw, dict):
            raise ValueError("Prompt profile must be a YAML mapping.")
        name, version, guidance = raw.get("name"), raw.get("version"), raw.get("system_guidance")
        if not all(isinstance(v, str) and v.strip() for v in (name, version, guidance)):
            raise ValueError("Prompt profile requires non-empty name, version, and system_guidance.")
        if len(guidance) > 12000:
            raise ValueError("system_guidance exceeds 12000 characters.")
        experiment = raw.get("experiment", "control")
        if not isinstance(experiment, str) or len(experiment) > 80:
            raise ValueError("experiment must be a short string.")
        return PromptProfile(name.strip(), version.strip(), guidance.strip(), experiment)

    def fingerprint(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()
