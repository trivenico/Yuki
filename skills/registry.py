"""Validated registry for Yuki's explicitly enabled skills."""
from __future__ import annotations

from typing import Iterable

from .base import Skill
from .file_skill import FileSkill
from .system_skill import SystemSkill
from .time_skill import TimeSkill


class SkillRegistry:
    """Owns a fixed set of named skills; duplicate names fail closed."""

    def __init__(self, skills: Iterable[Skill] = ()):
        self._skills: dict[str, Skill] = {}
        for skill in skills:
            self.register(skill)

    def register(self, skill: Skill) -> None:
        name = getattr(skill, "name", None)
        if not isinstance(name, str) or not name or not name.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Skill name must be a simple identifier.")
        if name in self._skills:
            raise ValueError(f"Duplicate skill name: {name}")
        if not callable(getattr(skill, "execute", None)):
            raise TypeError("Registered skills must implement execute().")
        self._skills[name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._skills))

    def describe(self) -> list[dict[str, str]]:
        return [{"name": s.name, "description": s.description, "risk": s.risk}
                for s in sorted(self._skills.values(), key=lambda item: item.name)]

    def __len__(self) -> int:
        return len(self._skills)

    @classmethod
    def defaults(cls, workspace):
        return cls((TimeSkill(), SystemSkill(), FileSkill(workspace)))
