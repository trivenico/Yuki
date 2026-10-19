"""Validated registry for Yuki's explicitly enabled skills."""
from __future__ import annotations
from pathlib import Path
from typing import Iterable
from .base import Skill
from .file_skill import FileSkill
from .system_skill import SystemSkill
from .time_skill import TimeSkill
from .interview import InterviewSkill
from .course_coach import CourseCoachSkill
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
from .prediction_engine import PredictionEngineSkill
from .multi_model_critique import MultiModelCritiqueSkill
from .ai_mentor import AIMentorSkill
from .ai_humanizer import AIHumanizerSkill
from .youtube_shorts import YouTubeShortsSkill
from .seo_geo_optimizer import SEOGEOOptimizerSkill
from .conversation_analyzer import ConversationAnalyzerSkill
from .vibe_coder import VibeCoderSkill
from .long_video_generator import LongVideoGeneratorSkill
from .video_generator import VideoGeneratorSkill
from .auto_personalizer import AutoPersonalizerSkill
from .chat_organizer import ChatOrganizerSkill
from .smart_home_full import SmartHomeFullSkill
from .device_scanner import DeviceScannerSkill
from .car_integration import CarIntegrationSkill
from .canvas_workspace import CanvasWorkspaceSkill
from .financial_manager import FinancialManagerSkill
from .health_agent import HealthAgentSkill
from .home_security import HomeSecuritySkill
from .messaging_expanded import MessagingExpandedSkill
from .tiered_autonomy_skill import TieredAutonomySkill
from .mcp_skill import MCPToolSkill
from companion.identity_manager import IdentityManager
from skills.role_skill import RoleSkill
from core.plugin_loader import PluginLoader


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
        description = getattr(skill, "description", None)
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Skill description must be a non-empty string.")
        risk = getattr(skill, "risk", "low")
        if risk not in {"low", "medium", "high"}:
            raise ValueError("Skill risk must be low, medium, or high.")
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
                 gmail_token="token.json", web_allowed_hosts=(), plugin_discovery_enabled=False, trusted_plugin_modules=None):
        root = Path(workspace).expanduser().resolve()
        registry = cls((
            TimeSkill(), SystemSkill(), FileSkill(root), InterviewSkill(), CourseCoachSkill(), WebSkill(allowed_hosts=web_allowed_hosts),
            GmailReadSkill(enabled=gmail_enabled, credentials_path=gmail_credentials, token_path=gmail_token),
            DeepReadSkill(allowed_hosts=web_allowed_hosts),
            KnowledgeSkill(root), HealthSkill(root), ExploitSuggesterSkill(),
            BrowserSkill(allowed_hosts=web_allowed_hosts), YouTubeSkill(), ImageGenerationSkill(), WebcamSkill(),
            WikiSkill(allowed_hosts=web_allowed_hosts), IdentitySkill(IdentityManager(root / "yuki_identities.json")), ProjectSkill(),
            ContentMarketerSkill(), SEOOptimizerSkill(), MonetizationAdvisorSkill(),
            PredictionEngineSkill(), MultiModelCritiqueSkill(), AIMentorSkill(root), AIHumanizerSkill(),
            YouTubeShortsSkill(), SEOGEOOptimizerSkill(), ConversationAnalyzerSkill(root), VibeCoderSkill(), LongVideoGeneratorSkill(),
            VideoGeneratorSkill(root), AutoPersonalizerSkill(root), ChatOrganizerSkill(root),
            SmartHomeFullSkill(), DeviceScannerSkill(root), CarIntegrationSkill(), CanvasWorkspaceSkill(root),
            FinancialManagerSkill(root), HealthAgentSkill(root), HomeSecuritySkill(root),
            MessagingExpandedSkill(root), TieredAutonomySkill(root),
            MCPToolSkill(allowed_hosts=web_allowed_hosts),
            RoleSkill(),
        ))
        if plugin_discovery_enabled:
            loader = PluginLoader(enabled=True, trusted_modules=trusted_plugin_modules)
            discovered = loader.discover()
            for skill in discovered["loaded"]:
                if skill.name not in registry._skills:
                    try:
                        registry.register(skill)
                    except (ValueError, TypeError):
                        continue
        return registry
