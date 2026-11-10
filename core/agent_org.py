"""Role-based agent-company orchestration primitives for Yuki.

The roster is a catalogue of specialist roles, not 100 continuously running LLMs.
Tasks are dispatched only to explicitly registered handlers. This module does not
spawn processes, call models, run shell commands, or perform external actions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from threading import RLock
from typing import Any, Callable, Optional
from uuid import uuid4


class Risk(str, Enum):
    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    EXTERNAL = "external"
    FINANCIAL = "financial"
    DEVICE_CONTROL = "device_control"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    BLOCKED = "blocked"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class AgentRole:
    role_id: str
    department: str
    title: str
    mission: str
    tier: str = "specialist"


@dataclass(frozen=True)
class AgentTask:
    task_id: str
    title: str
    objective: str
    role_id: str
    risk: Risk
    status: TaskStatus
    approval_required: bool
    created_by: str = "yuki_ceo"
    result_summary: str = ""


@dataclass(frozen=True)
class TaskResult:
    task: AgentTask
    ok: bool
    message: str


DEPARTMENTS = {
    "executive": ("CEO Chief of Staff", "Coordinate priorities, dependencies, and executive reporting."),
    "research": ("Research Analyst", "Find evidence, evaluate sources, and state uncertainty."),
    "earning": ("Earning Strategist", "Validate buyer problems, business models, and revenue hypotheses."),
    "product": ("Product Manager", "Turn user needs into measurable product requirements."),
    "design": ("Product Designer", "Develop accessible, coherent user experiences."),
    "frontend": ("Frontend Engineer", "Build maintainable, accessible interfaces."),
    "backend": ("Backend Engineer", "Build reliable APIs and domain services."),
    "platform": ("Platform Engineer", "Maintain developer tooling and shared runtime foundations."),
    "devops": ("DevOps Engineer", "Automate build, release, deployment, and rollback."),
    "sre": ("Site Reliability Engineer", "Improve reliability, observability, and incident response."),
    "security": ("Security Engineer", "Threat-model features and verify security boundaries."),
    "qa": ("QA Engineer", "Design functional, regression, and adversarial tests."),
    "data": ("Data Engineer", "Build governed data pipelines and data quality checks."),
    "ml": ("ML Engineer", "Evaluate models and improve inference quality and cost."),
    "agent": ("Agent Engineer", "Build bounded agents, tools, and task lifecycle management."),
    "memory": ("Memory Engineer", "Build user-controlled memory, retrieval, export, and deletion."),
    "voice": ("Voice Engineer", "Build opt-in speech interfaces with clear capture controls."),
    "automation": ("Automation Engineer", "Build durable workflows, schedules, retries, and recovery."),
    "integrations": ("Integration Engineer", "Connect approved APIs and MCP servers through adapters."),
    "support": ("Customer Support Specialist", "Create helpful support workflows and user documentation."),
    "legal": ("Compliance Analyst", "Flag legal, privacy, licensing, and policy questions for review."),
    "finance": ("Finance Analyst", "Track budgets and distinguish estimates from realized revenue."),
    "marketing": ("Growth Marketer", "Research positioning, distribution, and measurable experiments."),
    "sales": ("Sales Researcher", "Research prospects and prepare outreach drafts for approval."),
    "content": ("Content Strategist", "Plan evidence-led content and editorial workflows."),
    "documentation": ("Technical Writer", "Produce accurate, versioned product and operational docs."),
    "accessibility": ("Accessibility Specialist", "Review inclusive interaction and assistive technology support."),
    "performance": ("Performance Engineer", "Measure latency, throughput, resource usage, and regressions."),
    "database": ("Database Engineer", "Design migrations, backups, concurrency, and restore tests."),
    "release": ("Release Manager", "Coordinate release evidence, change control, and rollback readiness."),
}


ELITE_COUNCIL = (
    ("chief_architect", "Chief Systems Architect", "Architecture coherence, interfaces, and long-term design."),
    ("chief_engineer", "Principal Software Engineer", "Code quality, implementation plans, and technical review."),
    ("chief_security", "Chief Security Officer", "Threat modeling, authorization, privacy, and incident prevention."),
    ("chief_reliability", "Chief Reliability Officer", "Reliability, recovery, observability, and SLOs."),
    ("chief_product", "Chief Product Officer", "User outcomes, prioritization, and acceptance criteria."),
    ("chief_research", "Chief Research Officer", "Evidence quality, source provenance, and uncertainty."),
    ("chief_data_ai", "Chief AI and Data Officer", "Model evaluation, memory architecture, and data governance."),
    ("chief_growth", "Chief Growth and Earning Officer", "Ethical earning experiments and measurable outcomes."),
    ("chief_quality", "Chief Quality Officer", "Testing strategy, regressions, and release gates."),
    ("chief_privacy", "Chief Privacy and Trust Officer", "Consent, data minimization, retention, and user control."),
)


def build_role_catalog() -> tuple[AgentRole, ...]:
    """Generate 120 distinct role profiles from 30 departments and 4 levels."""
    levels = (
        ("lead", "Lead"),
        ("senior", "Senior"),
        ("specialist", "Specialist"),
        ("associate", "Associate"),
    )
    roles = []
    for department, (title, mission) in DEPARTMENTS.items():
        for tier, prefix in levels:
            roles.append(AgentRole(
                role_id=f"{department}_{tier}",
                department=department,
                title=f"{prefix} {title}",
                mission=mission,
                tier=tier,
            ))
    return tuple(roles)


def build_elite_council() -> tuple[AgentRole, ...]:
    return tuple(
        AgentRole(role_id=role_id, department="elite_council", title=title,
                  mission=mission, tier="elite")
        for role_id, title, mission in ELITE_COUNCIL
    )


class AgentOrganization:
    """A CEO-style role registry and bounded task dispatcher.

    This is intentionally a lightweight, in-memory orchestration core. Durable
    queues, real model-backed workers, parallel execution, and cross-process
    coordination must be added as separate tested adapters.
    """

    def __init__(
        self,
        *,
        roles: Optional[tuple[AgentRole, ...]] = None,
        elite_council: Optional[tuple[AgentRole, ...]] = None,
        handlers: Optional[dict[str, Callable[[AgentTask], str]]] = None,
        approver: Optional[Callable[[AgentTask], bool]] = None,
        max_tasks: int = 1000,
    ):
        if isinstance(max_tasks, bool) or not isinstance(max_tasks, int) or not 1 <= max_tasks <= 10000:
            raise ValueError("max_tasks must be an integer between 1 and 10000")
        self.roles = {r.role_id: r for r in (roles if roles is not None else build_role_catalog())}
        self.elite_council = {r.role_id: r for r in (elite_council if elite_council is not None else build_elite_council())}
        if set(self.roles) & set(self.elite_council):
            raise ValueError("Regular role IDs must not collide with elite council IDs")
        self.handlers = dict(handlers or {})
        self.approver = approver
        self.max_tasks = max_tasks
        self._tasks: dict[str, AgentTask] = {}
        self._lock = RLock()

    def organization_chart(self) -> dict[str, Any]:
        return {
            "ceo": {"title": "Yuki CEO / Chief Orchestrator", "role": "prioritize, delegate, review, and report"},
            "elite_council": [asdict(role) for role in self.elite_council.values()],
            "specialist_role_count": len(self.roles),
            "specialist_roles": [asdict(role) for role in self.roles.values()],
            "registered_worker_count": len(self.handlers),
            "active_llm_processes": 0,
            "note": "Role profiles are not running agents. Only explicitly registered handlers can execute tasks.",
        }

    def register_worker(self, role_id: str, handler: Callable[[AgentTask], str]) -> None:
        if role_id not in self.roles and role_id not in self.elite_council:
            raise ValueError("Worker role is not in the approved role catalogue")
        if not callable(handler):
            raise TypeError("handler must be callable")
        with self._lock:
            self.handlers[role_id] = handler

    def create_task(
        self, *, title: str, objective: str, role_id: str,
        risk: Risk = Risk.READ_ONLY, created_by: str = "yuki_ceo",
    ) -> AgentTask:
        for field, value in (("title", title), ("objective", objective), ("role_id", role_id), ("created_by", created_by)):
            if not isinstance(value, str) or not value.strip() or len(value) > 2000:
                raise ValueError(f"{field} must be non-empty text up to 2000 characters")
        if role_id not in self.roles and role_id not in self.elite_council:
            raise ValueError("Unknown role; task cannot be delegated")
        if not isinstance(risk, Risk):
            raise ValueError("risk must be a Risk enum value")
        approval_required = risk is not Risk.READ_ONLY
        with self._lock:
            if len(self._tasks) >= self.max_tasks:
                raise OverflowError("Task capacity reached; drain or archive tasks before adding more")
            task = AgentTask(
                task_id=str(uuid4()), title=title.strip(), objective=objective.strip(),
                role_id=role_id, risk=risk, status=TaskStatus.QUEUED,
                approval_required=approval_required, created_by=created_by.strip(),
            )
            self._tasks[task.task_id] = task
            return task

    def get_task(self, task_id: str) -> Optional[AgentTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, status: Optional[TaskStatus] = None) -> list[AgentTask]:
        with self._lock:
            items = list(self._tasks.values())
        if status is not None:
            items = [item for item in items if item.status is status]
        return items

    def dispatch(self, task_id: str) -> TaskResult:
        """Run one registered handler after checking approval; fail closed."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return TaskResult(
                    AgentTask(task_id=task_id, title="", objective="", role_id="", risk=Risk.READ_ONLY,
                              status=TaskStatus.FAILED, approval_required=False),
                    False, "Task not found",
                )
            if task.status is not TaskStatus.QUEUED:
                return TaskResult(task, False, "Task is not queued")
            handler = self.handlers.get(task.role_id)
            if handler is None:
                blocked = AgentTask(**{**asdict(task), "status": TaskStatus.BLOCKED,
                                       "result_summary": "No worker handler registered"})
                self._tasks[task_id] = blocked
                return TaskResult(blocked, False, "No worker handler registered for this role")
            if task.approval_required:
                if self.approver is None:
                    blocked = AgentTask(**{**asdict(task), "status": TaskStatus.BLOCKED,
                                           "result_summary": "No approver configured"})
                    self._tasks[task_id] = blocked
                    return TaskResult(blocked, False, "Approval unavailable; failed closed")
                try:
                    approved = self.approver(task) is True
                except Exception:
                    approved = False
                if not approved:
                    blocked = AgentTask(**{**asdict(task), "status": TaskStatus.BLOCKED,
                                           "result_summary": "Action not approved"})
                    self._tasks[task_id] = blocked
                    return TaskResult(blocked, False, "Action not approved")
            running = AgentTask(**{**asdict(task), "status": TaskStatus.RUNNING})
            self._tasks[task_id] = running

        # Never execute user-provided code. Only a pre-registered trusted handler
        # is called, outside the lock to avoid deadlocks during longer tasks.
        try:
            summary = handler(running)
            if not isinstance(summary, str):
                raise TypeError("Worker handler must return a string summary")
            summary = summary.strip()[:4000]
            succeeded = AgentTask(**{**asdict(running), "status": TaskStatus.SUCCEEDED,
                                     "result_summary": summary})
            with self._lock:
                self._tasks[task_id] = succeeded
            return TaskResult(succeeded, True, "Task completed")
        except Exception as exc:
            failed = AgentTask(**{**asdict(running), "status": TaskStatus.FAILED,
                                  "result_summary": f"Worker failed: {type(exc).__name__}"})
            with self._lock:
                self._tasks[task_id] = failed
            return TaskResult(failed, False, "Worker failed; details redacted")

