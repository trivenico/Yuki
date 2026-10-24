"""Core agent boundary for Yuki's provider-independent architecture."""

from providers import MockProvider
from core.guarded_skill_runtime import OneShotSkillApprovals, invoke_registered_skill


class YukiAgent:
    """Send prompts to a provider and expose an explicitly configured skill boundary."""

    def __init__(self, provider=None, *, skill_registry=None, skill_audit=None):
        self.provider = provider if provider is not None else MockProvider()
        self._skill_registry = skill_registry
        self._skill_audit = skill_audit
        self._skill_approvals = OneShotSkillApprovals()

    def set_provider(self, provider):
        """Replace the active adapter after validating its interface."""
        if not callable(getattr(provider, "generate", None)) or not callable(
            getattr(provider, "is_available", None)
        ):
            raise TypeError("Provider must implement generate() and is_available().")
        self.provider = provider

    def configure_skill_runtime(self, registry, *, audit=None):
        """Explicitly attach a registry; no module discovery/import is performed."""
        if not callable(getattr(registry, "get", None)):
            raise TypeError("Skill registry must implement get(name).")
        self._skill_registry = registry
        self._skill_audit = audit
        self._skill_approvals.clear()

    def approve_skill_once(self, skill_name):
        """Approve exactly one future invocation of a registered skill."""
        if self._skill_registry is None:
            raise RuntimeError("Skill runtime is not configured.")
        if self._skill_registry.get(skill_name) is None:
            raise ValueError("Cannot approve a skill that is not registered.")
        self._skill_approvals.approve(skill_name)

    def invoke_skill(self, skill_name, argument=""):
        """Invoke only via the shared guarded runtime; never infer execution from LLM text."""
        if self._skill_registry is None:
            from core.guarded_skill_runtime import SkillInvocationResult
            return SkillInvocationResult(
                False, "Skill runtime is not configured.", str(skill_name)[:128], "not_configured"
            )
        return invoke_registered_skill(
            self._skill_registry,
            skill_name,
            argument,
            approvals=self._skill_approvals,
            audit=self._skill_audit,
        )

    def status(self):
        try:
            return "Provider is ready." if self.provider.is_available() else "Provider is unavailable."
        except Exception:
            return "Provider status is unknown."

    def ask(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip():
            return "Yuki: Please enter a message."
        try:
            if not self.provider.is_available():
                return "Yuki: The AI provider is unavailable."
            result = self.provider.generate(prompt.strip())
            if not isinstance(result, str):
                return "Yuki: The provider returned an unsupported response."
            return result
        except Exception:
            return "Yuki: The provider encountered an error."
