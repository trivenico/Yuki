"""Metadata-only virtual office catalog. Profiles never grant permissions or execute tools."""
from __future__ import annotations
from dataclasses import dataclass
import re
from typing import Iterable

@dataclass(frozen=True)
class CouncilMember:
    name: str
    council: str
    focus: str

@dataclass(frozen=True)
class EnterpriseRole:
    name: str
    department: str
    focus: str
    risk: str = "low"

COUNCILS = {
    "genius": (("Newton", "scientific reasoning and quantitative checks"), ("da_vinci", "design synthesis"), ("Einstein", "conceptual models"), ("Lovelace", "algorithmic thinking"), ("Tesla", "systems concepts"), ("Feynman", "clear explanations and falsification"), ("Curie", "evidence quality"), ("Turing", "computation and formal reasoning")),
    "business": (("Bezos", "customer focus and operations"), ("Gates", "platform strategy"), ("Musk", "first-principles constraints"), ("Ambani", "scale and ecosystem strategy"), ("Buffett", "capital discipline and downside risk"), ("Jobs", "product simplicity"), ("Zuckerberg", "distribution and network effects"), ("Ma", "merchant enablement")),
    "ceo": (("Nadella", "learning culture"), ("Cook", "operations and supply chain"), ("Pichai", "product portfolio and responsible AI"), ("Huang", "compute platforms"), ("Barra", "manufacturing and safety"), ("Dimon", "risk governance"), ("Wojcicki", "creator ecosystems"), ("Nooyi", "stakeholder strategy")),
    "developer": (("Torvalds", "kernel architecture and review"), ("Guido", "readability"), ("Carmack", "performance"), ("Dean", "large-scale systems"), ("Lamport", "distributed systems correctness"), ("Matsumoto", "developer ergonomics"), ("Berners_Lee", "open web interoperability"), ("Kernighan", "small tools and clarity")),
}

ROLES = (
    EnterpriseRole("CEO", "executive", "prioritization and accountability"),
    EnterpriseRole("Business Director", "executive", "business goals and metrics"),
    EnterpriseRole("Marketing Manager", "marketing", "campaign strategy"),
    EnterpriseRole("Content Strategist", "marketing", "editorial plans and content quality"),
    EnterpriseRole("Financial Advisor", "business", "budget scenarios and financial education; not regulated advice"),
    EnterpriseRole("Senior Cybersecurity Engineer", "security", "defensive threat modeling and remediation"),
    EnterpriseRole("Security Auditor", "security", "control review and evidence-based findings"),
    EnterpriseRole("SEO Manager", "marketing", "search visibility"),
    EnterpriseRole("Social Media Manager", "marketing", "channel plans and post drafts"),
    EnterpriseRole("Senior Full-Stack Engineer", "engineering", "end-to-end implementation"),
    EnterpriseRole("Onboarding Engineer", "engineering", "setup and developer experience"),
    EnterpriseRole("Debugging Engineer", "engineering", "reproduction and root-cause analysis"),
    EnterpriseRole("Performance Engineer", "engineering", "profiling and optimization"),
    EnterpriseRole("Software Architect", "engineering", "interfaces and maintainability"),
    EnterpriseRole("Systems Architect", "engineering", "deployment and reliability"),
    EnterpriseRole("Frontend Engineer", "engineering", "accessible UI implementation"),
    EnterpriseRole("UX Researcher", "design", "user needs and usability evidence"),
    EnterpriseRole("Technical Lead", "engineering", "technical decisions and review quality"),
    EnterpriseRole("DevOps Engineer", "platform", "CI/CD, observability, deployment and rollback"),
    EnterpriseRole("Data Scientist", "data", "data quality and experiments"),
    EnterpriseRole("QA Tester", "quality", "reproducible tests and regression coverage"),
    EnterpriseRole("Accessibility Tester", "quality", "keyboard, screen-reader and WCAG checks"),
    EnterpriseRole("Mobile Tester", "quality", "mobile layout and device compatibility"),
    EnterpriseRole("API Tester", "quality", "contract, auth and rate-limit tests"),
    EnterpriseRole("Executive Assistant", "operations", "scheduling and coordination"),
    EnterpriseRole("Personal Chief of Staff", "operations", "priorities and daily briefs"),
    EnterpriseRole("TriveniCo Guardian", "operations", "approved business workflows and policy checks"),
    EnterpriseRole("Security Researcher", "security", "authorized lab research and responsible disclosure"),
)

def council_members() -> tuple[CouncilMember, ...]:
    """Return 32 metadata profiles; no workers are started."""
    return tuple(CouncilMember(name, council, focus) for council, members in COUNCILS.items() for name, focus in members)

def list_roles() -> tuple[EnterpriseRole, ...]:
    return ROLES

def build_role_brief(role_name: str, task: str) -> dict[str, str]:
    """Create an inspectable brief; it does not execute or authorize the task."""
    if not isinstance(role_name, str) or not role_name.strip():
        raise ValueError("role_name is required.")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task is required.")
    if len(task) > 4000:
        raise ValueError("task exceeds the 4000-character limit.")
    role = next((item for item in ROLES if item.name.casefold() == role_name.strip().casefold()), None)
    if role is None:
        raise ValueError("Unknown role.")
    safe_task = re.sub(r"[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f]", "", task).strip()
    return {"role": role.name, "department": role.department, "focus": role.focus, "risk": role.risk, "task": safe_task,
            "permission_note": "This brief grants no tool, file, network, or account permissions. Human approval and runtime policy still apply."}

def plan_review_team(task: str, roles: Iterable[str]) -> tuple[dict[str, str], ...]:
    """Build role briefs without spawning agents."""
    if isinstance(roles, str):
        raise TypeError("roles must be an iterable of role names, not a string.")
    return tuple(build_role_brief(role, task) for role in roles)