"""Validated registry for Yuki's explicitly enabled skills."""
from __future__ import annotations
from pathlib import Path
from typing import Iterable
from .base import Skill
from .file_skill import FileSkill
from .system_skill import SystemSkill
from .time_skill import TimeSkill
from .interview import InterviewSkill
from .web_skill import WebSkill
from .email_skill import GmailReadSkill
from .web_deep_read import DeepReadSkill
from .knowledge_skill import KnowledgeSkill
from .health_skill import HealthSkill
from .exploit_suggester import ExploitSuggesterSkill
from .browser_skill import BrowserSkill
from .youtube_skill import YouTubeSkill
from .image_skill import ImageGenerationSkill
from .webcam_skill import WebcamSkill
from .wiki_skill import WikiSkill
from .identity_skill import IdentitySkill
from .project_skill import ProjectSkill
from .content_marketer import ContentMarketerSkill
from .seo_optimizer import SEOOptimizerSkill
from .monetization_advisor import MonetizationAdvisorSkill


class SkillRegistry:
    def __init__(self, skills: Iterable[Skill] = ()):
        self._skills = {}
        for skill in skills:
            self.register(skill)

    def register(self, skill):
        name = getattr(skill, "name", None)
        if not isinstance(name, str) or not name or not name.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Skill name must be a simple identifier.")
        if name in self._skills:
            raise ValueError(f"Duplicate skill name: {name}")
        if not callable(getattr(skill, "execute", None)):
            raise TypeError("Registered skills must implement execute().")
        self._skills[name] = skill

    def get(self, name):
        return self._skills.get(name)

    def names(self):
        return tuple(sorted(self._skills))

    def describe(self):
        return [{"name": s.name, "description": s.description, "risk": s.risk}
                for s in sorted(self._skills.values(), key=lambda item: item.name)]

    def __len__(self):
        return len(self._skills)

    @classmethod
    def defaults(cls, workspace, *, gmail_enabled=False, gmail_credentials="credentials.json",
                 gmail_token="token.json", web_allowed_hosts=()):
        root = Path(workspace).expanduser().resolve()
        return cls((
            TimeSkill(), SystemSkill(), FileSkill(root), InterviewSkill(), WebSkill(allowed_hosts=web_allowed_hosts),
            GmailReadSkill(enabled=gmail_enabled, credentials_path=gmail_credentials, token_path=gmail_token),
            DeepReadSkill(allowed_hosts=web_allowed_hosts),
            KnowledgeSkill(root), HealthSkill(root), ExploitSuggesterSkill(),
            BrowserSkill(allowed_hosts=web_allowed_hosts), YouTubeSkill(), ImageGenerationSkill(), WebcamSkill(),
            WikiSkill(allowed_hosts=web_allowed_hosts), IdentitySkill(), ProjectSkill(),
            ContentMarketerSkill(), SEOOptimizerSkill(), MonetizationAdvisorSkill(),
        ))
