"""Safe interaction-role profiles; roles never grant tools or permissions."""
from dataclasses import dataclass

@dataclass(frozen=True)
class RoleProfile:
    name: str
    description: str
    style: str
    focus: tuple[str, ...]

class RoleEngine:
    """Selects response guidance only. PermissionGate remains authoritative."""
    PROFILES = {
        "companion": RoleProfile("companion", "Supportive personal companion", "warm, respectful and conversational", ("listen", "clarify", "support")),
        "tutor": RoleProfile("tutor", "Course tutor and lab coach", "patient, explanatory and encouraging", ("teach concepts", "guided practice", "check understanding")),
        "interviewer": RoleProfile("interviewer", "Mock interview coach", "professional, probing and constructive", ("ask one question at a time", "give evidence-based feedback", "identify practice areas")),
        "developer": RoleProfile("developer", "Senior software engineering partner", "precise, pragmatic and test-driven", ("inspect requirements", "propose small changes", "test and review")),
        "security": RoleProfile("security", "Authorised defensive security coach", "careful, scope-aware and evidence-led", ("confirm authorisation", "explain findings", "prefer defensive remediation")),
        "marketing": RoleProfile("marketing", "Content and business planning partner", "clear, audience-aware and evidence-conscious", ("draft content", "evaluate options", "avoid unsupported claims")),
    }
    def __init__(self, initial="companion"):
        if initial not in self.PROFILES:
            raise ValueError("Unknown role profile.")
        self._active = initial
    def current(self):
        p = self.PROFILES[self._active]
        return {"name": p.name, "description": p.description, "style": p.style, "focus": list(p.focus)}
    def set_role(self, name):
        if not isinstance(name, str) or name.strip().lower() not in self.PROFILES:
            raise ValueError("Unknown role. Choose: " + ", ".join(sorted(self.PROFILES)))
        self._active = name.strip().lower()
        return self.current()
    def available(self):
        return [{"name": p.name, "description": p.description} for p in self.PROFILES.values()]
