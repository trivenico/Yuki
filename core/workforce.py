"""Validated, metadata-only registry for Yuki's 100-person AI workforce.

The registry describes employees and recommends eligible assignees. It does not
instantiate models, execute tools, grant permissions, or imply that 100 agents
are running concurrently. Execution remains behind Yuki's existing policy and
approval boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class AgentProfile:
    employee_id: str
    name: str
    department: str
    role: str
    capabilities: frozenset[str]
    reports_to: str
    autonomy_tier: int = 1
    permissions: frozenset[str] = frozenset()
    status: str = "available"
    active_tasks: int = 0
    max_concurrent_tasks: int = 2
    quality_score: float = 0.75
    success_rate: float = 0.75
    tasks_completed: int = 0

    def __post_init__(self):
        if not self.employee_id or not self.employee_id.strip():
            raise ValueError("employee_id must be non-empty")
        if not self.name.strip() or not self.department.strip() or not self.role.strip():
            raise ValueError("name, department, and role must be non-empty")
        if not self.capabilities or any(not x.strip() for x in self.capabilities):
            raise ValueError("at least one non-empty capability is required")
        if not self.reports_to.strip():
            raise ValueError("reports_to must be non-empty")
        if self.autonomy_tier not in range(4):
            raise ValueError("autonomy_tier must be between 0 and 3")
        if self.status not in {"available", "busy", "offline", "disabled"}:
            raise ValueError("unsupported employee status")
        if self.active_tasks < 0 or self.max_concurrent_tasks < 1:
            raise ValueError("task counts must be non-negative and capacity positive")
        if self.active_tasks > self.max_concurrent_tasks:
            raise ValueError("active_tasks cannot exceed max_concurrent_tasks")
        for field_name in ("quality_score", "success_rate"):
            value = getattr(self, field_name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be between 0 and 1")
        if self.tasks_completed < 0:
            raise ValueError("tasks_completed must be non-negative")


# Each entry defines one department and ten distinct employee specializations.
# The first employee is the department lead; the CEO is a separate role.
_DEPARTMENTS = (
    ("executive", "Executive Operations", (
        ("Chief Operating Officer", "operations strategy planning governance"),
        ("Chief of Staff", "coordination executive_briefing prioritization"),
        ("Program Director", "program_management delivery dependencies"),
        ("Business Operations Manager", "process_design operations metrics"),
        ("Chief Technology Officer", "technology_strategy architecture"),
        ("Chief Information Officer", "information_governance systems"),
        ("Chief Financial Officer", "financial_strategy budgeting"),
        ("Chief Product Officer", "product_strategy portfolio"),
        ("People Operations Director", "workforce_planning performance"),
        ("Strategy Analyst", "market_analysis strategic_planning"),
    )),
    ("engineering", "Engineering", (
        ("Engineering Director", "engineering leadership architecture delivery"),
        ("Principal Backend Engineer", "python backend api databases"),
        ("Frontend Engineer", "typescript frontend accessibility ui"),
        ("Full-Stack Engineer", "python typescript api frontend"),
        ("Platform Engineer", "platform infrastructure reliability"),
        ("DevOps Engineer", "ci_cd deployment automation"),
        ("Site Reliability Engineer", "observability incident_response reliability"),
        ("Database Engineer", "postgresql data_modeling migrations"),
        ("Mobile Engineer", "ios android mobile"),
        ("QA Automation Engineer", "pytest testing regression automation"),
    )),
    ("ai_research", "AI Research", (
        ("AI Research Director", "research leadership evaluation"),
        ("LLM Research Engineer", "llm inference prompting"),
        ("Agent Systems Engineer", "agent orchestration tool_use"),
        ("RAG Engineer", "retrieval embeddings vector_search"),
        ("Model Evaluation Engineer", "benchmarks evals reliability"),
        ("Model Optimization Engineer", "quantization inference optimization"),
        ("Data Curation Engineer", "datasets labeling data_quality"),
        ("Applied Scientist", "experimentation statistics research"),
        ("MLOps Engineer", "model_serving deployment monitoring"),
        ("AI Safety Researcher", "alignment safety evaluations"),
    )),
    ("security", "Security", (
        ("Security Director", "security governance risk"),
        ("Application Security Engineer", "appsec code_review threat_modeling"),
        ("Cloud Security Engineer", "cloud_security iam hardening"),
        ("Security Operations Analyst", "detection triage incident_response"),
        ("Vulnerability Analyst", "dependency_scanning vulnerability_management"),
        ("Privacy Engineer", "privacy data_minimization"),
        ("Identity Engineer", "authentication authorization iam"),
        ("Security Tester", "authorized_testing security_validation"),
        ("Compliance Security Analyst", "controls evidence compliance"),
        ("Security Automation Engineer", "security_automation policy_as_code"),
    )),
    ("product", "Product", (
        ("Product Director", "product_strategy roadmaps"),
        ("Product Manager", "requirements prioritization discovery"),
        ("UX Researcher", "user_research usability"),
        ("Product Designer", "interaction_design prototyping"),
        ("UI Designer", "visual_design design_systems"),
        ("Technical Writer", "documentation information_architecture"),
        ("QA Analyst", "acceptance_testing test_cases"),
        ("Accessibility Specialist", "wcag accessibility audits"),
        ("Product Analyst", "product_analytics metrics"),
        ("Release Manager", "release_planning change_management"),
    )),
    ("finance", "Finance", (
        ("Finance Director", "financial_governance forecasting"),
        ("Financial Controller", "accounting reconciliation"),
        ("FP&A Analyst", "forecasting financial_modeling"),
        ("Budget Manager", "budgeting cost_control"),
        ("Procurement Analyst", "procurement vendor_analysis"),
        ("Revenue Analyst", "revenue_analysis reporting"),
        ("Expense Auditor", "expense_audit controls"),
        ("Pricing Analyst", "pricing unit_economics"),
        ("Treasury Analyst", "cash_flow treasury"),
        ("Financial Operations Specialist", "invoicing financial_operations"),
    )),
    ("marketing", "Marketing", (
        ("Marketing Director", "marketing_strategy brand"),
        ("SEO Specialist", "seo keyword_research"),
        ("Content Strategist", "content_strategy editorial"),
        ("Copywriter", "copywriting messaging"),
        ("Social Media Manager", "social_media community"),
        ("Growth Marketer", "growth_experiments acquisition"),
        ("Performance Marketing Analyst", "paid_media attribution"),
        ("Brand Designer", "branding visual_identity"),
        ("Marketing Researcher", "market_research competitor_analysis"),
        ("Email Marketing Specialist", "email_campaigns lifecycle"),
    )),
    ("sales", "Sales", (
        ("Sales Director", "sales_strategy pipeline"),
        ("Business Development Manager", "business_development partnerships"),
        ("Lead Research Analyst", "lead_generation prospecting"),
        ("Sales Operations Analyst", "crm sales_operations"),
        ("Account Executive", "discovery negotiation proposals"),
        ("Solutions Consultant", "solution_design technical_sales"),
        ("Customer Success Manager", "customer_success retention"),
        ("Customer Support Specialist", "support troubleshooting"),
        ("Partnership Manager", "partner_management alliances"),
        ("Revenue Operations Analyst", "revenue_operations forecasting"),
    )),
    ("legal_compliance", "Legal & Compliance", (
        ("Legal Director", "legal_governance contract_review"),
        ("Commercial Contracts Analyst", "contracts commercial_terms"),
        ("Privacy Compliance Analyst", "privacy_regulations data_protection"),
        ("Regulatory Researcher", "regulatory_research monitoring"),
        ("Policy Analyst", "policy_design governance"),
        ("Risk Manager", "risk_assessment controls"),
        ("IP Research Analyst", "intellectual_property licensing"),
        ("Vendor Compliance Analyst", "vendor_due_diligence compliance"),
        ("Records Governance Specialist", "records_retention governance"),
        ("Ethics & AI Governance Analyst", "ai_governance ethics"),
    )),
    ("innovation", "Research & Innovation", (
        ("Innovation Director", "innovation_strategy research_portfolio"),
        ("Technology Scout", "technology_scanning trend_analysis"),
        ("Scientific Researcher", "scientific_research literature_review"),
        ("Experimentation Engineer", "prototyping experimentation"),
        ("Patent Landscape Analyst", "patent_research prior_art"),
        ("New Ventures Analyst", "venture_analysis business_models"),
        ("Open Source Researcher", "open_source research"),
        ("Knowledge Engineer", "knowledge_graphs knowledge_management"),
        ("Emerging Technology Analyst", "emerging_technology forecasting"),
        ("Research Operations Specialist", "research_operations reproducibility"),
    )),
)


def default_workforce() -> tuple[AgentProfile, ...]:
    """Build the deterministic 100-profile directory; no models are started."""
    employees = []
    for department_id, department_name, roles in _DEPARTMENTS:
        lead_id = f"{department_id.upper()}-001"
        for index, (role, capability_text) in enumerate(roles, start=1):
            employee_id = f"{department_id.upper()}-{index:03d}"
            capabilities = frozenset(capability_text.split())
            employees.append(AgentProfile(
                employee_id=employee_id,
                name=role,
                department=department_name,
                role=role,
                capabilities=capabilities,
                reports_to="YUKI-CEO" if index == 1 else lead_id,
            ))
    return tuple(employees)


class AgentRegistry:
    """In-memory metadata registry with deterministic, permission-aware matching."""

    def __init__(self, employees: Optional[Iterable[AgentProfile]] = None):
        self._employees: dict[str, AgentProfile] = {}
        for employee in employees if employees is not None else default_workforce():
            self.register(employee)

    def register(self, employee: AgentProfile) -> None:
        if not isinstance(employee, AgentProfile):
            raise TypeError("employee must be an AgentProfile")
        if employee.employee_id in self._employees:
            raise ValueError(f"duplicate employee_id: {employee.employee_id}")
        self._employees[employee.employee_id] = employee

    def get(self, employee_id: str) -> Optional[AgentProfile]:
        return self._employees.get(employee_id)

    def list_employees(self, *, department: Optional[str] = None) -> tuple[AgentProfile, ...]:
        employees = self._employees.values()
        if department is not None:
            employees = (e for e in employees if e.department.casefold() == department.casefold())
        return tuple(sorted(employees, key=lambda e: e.employee_id))

    def recommend(
        self,
        required_capabilities: Iterable[str],
        *,
        required_permissions: Iterable[str] = (),
        max_autonomy_tier: int = 1,
        limit: int = 5,
    ) -> tuple[AgentProfile, ...]:
        """Return eligible candidates; this is ranking, never authorization."""
        required = frozenset(required_capabilities)
        permissions = frozenset(required_permissions)
        if not required or any(not isinstance(x, str) or not x.strip() for x in required):
            raise ValueError("required_capabilities must contain non-empty strings")
        if any(not isinstance(x, str) or not x.strip() for x in permissions):
            raise ValueError("required_permissions must contain non-empty strings")
        if max_autonomy_tier not in range(4):
            raise ValueError("max_autonomy_tier must be between 0 and 3")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")

        candidates = [
            employee for employee in self._employees.values()
            if employee.status == "available"
            and employee.active_tasks < employee.max_concurrent_tasks
            and employee.autonomy_tier <= max_autonomy_tier
            and required.issubset(employee.capabilities)
            and permissions.issubset(employee.permissions)
        ]

        def score(employee: AgentProfile) -> tuple[float, str]:
            capacity = 1 - employee.active_tasks / employee.max_concurrent_tasks
            experience = min(employee.tasks_completed / 100, 1)
            value = (
                0.40 * employee.quality_score
                + 0.30 * employee.success_rate
                + 0.20 * capacity
                + 0.10 * experience
            )
            return (-value, employee.employee_id)

        return tuple(sorted(candidates, key=score)[:limit])

    def __len__(self) -> int:
        return len(self._employees)
