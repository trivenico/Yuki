"""Machine-readable catalog of Yuki's planned specialist capabilities.

Catalog entries describe intended responsibilities; they are not executable agents.
Only reviewed handlers registered with the runtime may perform work.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Capability:
    capability_id: str
    name: str
    domain: str
    description: str
    status: str = "planned"
    execution_mode: str = "not_implemented"
    permissions: tuple[str, ...] = ()
    approval_required: bool = True


# id|domain|name|description
_ROWS = """
chief-of-staff|executive|Chief-of-Staff|Break goals into milestones, owners, and checkpoints.
daily-briefing|executive|Daily-Briefing|Summarize approved calendar, tasks, and selected updates.
task-manager|executive|Task-Manager|Create, prioritize, and track personal tasks.
calendar-planner|executive|Calendar-Planner|Propose schedules and resolve conflicts with approval.
reminder-manager|executive|Reminder-Manager|Manage explicit reminders and recurring routines.
decision-analyst|executive|Decision-Analyst|Compare options using stated criteria and evidence.
meeting-preparer|executive|Meeting-Preparer|Assemble agendas and approved background material.
meeting-notes|executive|Meeting-Notes|Summarize supplied notes and recordings with action items.
goal-coach|executive|Goal-Coach|Translate goals into measurable plans and reviews.
personal-search|executive|Personal-Search|Retrieve information from owner-approved sources.
conversation-companion|knowledge|Conversation-Companion|Provide context-aware conversational assistance.
memory-curator|knowledge|Memory-Curator|Manage owner-approved memories and retention.
knowledge-indexer|knowledge|Knowledge-Indexer|Index explicitly selected local documents.
knowledge-retriever|knowledge|Knowledge-Retriever|Retrieve relevant passages with source references.
research-analyst|knowledge|Research-Analyst|Synthesize sources and distinguish facts from inference.
fact-checker|knowledge|Fact-Checker|Verify claims against independent trusted sources.
tutor|knowledge|Tutor|Explain concepts at a requested level.
quiz-master|knowledge|Quiz-Master|Generate and grade practice questions with explanations.
language-coach|knowledge|Language-Coach|Support translation, vocabulary, and language practice.
writing-editor|knowledge|Writing-Editor|Draft, revise, and proofread requested content.
product-analyst|engineering|Product-Analyst|Convert user needs into requirements and acceptance criteria.
system-architect|engineering|System-Architect|Design components, boundaries, and integration contracts.
frontend-engineer|engineering|Frontend-Engineer|Implement accessible user-interface components.
backend-engineer|engineering|Backend-Engineer|Implement APIs, services, and domain logic.
api-designer|engineering|API-Designer|Define versioned and documented API contracts.
database-engineer|engineering|Database-Engineer|Design schemas, indexes, migrations, and data access.
code-reviewer|engineering|Code-Reviewer|Review diffs for correctness, maintainability, and regressions.
test-engineer|engineering|Test-Engineer|Create unit, integration, and acceptance tests.
debugging-engineer|engineering|Debugging-Engineer|Isolate failures and produce reproducible diagnoses.
refactoring-engineer|engineering|Refactoring-Engineer|Improve structure while preserving behavior.
devops-engineer|delivery|DevOps-Engineer|Maintain repeatable build and deployment workflows.
ci-guardian|delivery|CI-Guardian|Inspect pipeline results and enforce release gates.
release-manager|delivery|Release-Manager|Prepare release notes, checklists, and rollback plans.
observability-engineer|delivery|Observability-Engineer|Define useful logs, metrics, and traces.
incident-commander|delivery|Incident-Commander|Coordinate evidence-led incident response.
performance-engineer|delivery|Performance-Engineer|Profile bottlenecks and recommend measured optimizations.
capacity-planner|delivery|Capacity-Planner|Estimate compute, storage, and concurrency needs.
reliability-engineer|delivery|Reliability-Engineer|Assess failure modes, recovery, and service objectives.
backup-operator|delivery|Backup-Operator|Validate backups and owner-approved restore procedures.
cost-optimizer|delivery|Cost-Optimizer|Analyze model, cloud, and infrastructure costs.
security-architect|security|Security-Architect|Maintain threat models and security design reviews.
dependency-auditor|security|Dependency-Auditor|Identify vulnerable or unmaintained dependencies.
static-analyzer|security|Static-Analyzer|Inspect code for common security and correctness defects.
secrets-guardian|security|Secrets-Guardian|Detect accidental secret exposure without revealing values.
access-control-auditor|security|Access-Control-Auditor|Review permissions and least-privilege boundaries.
privacy-reviewer|security|Privacy-Reviewer|Assess collection, retention, and data minimization.
prompt-injection-analyst|security|Prompt-Injection-Analyst|Flag hostile instructions in untrusted content.
secure-config-reviewer|security|Secure-Config-Reviewer|Inspect deployment and application configuration.
incident-forensics|security|Incident-Forensics|Organize supplied logs and preserve evidence provenance.
remediation-planner|security|Remediation-Planner|Prioritize defensive fixes with validation steps.
web-researcher|research|Web-Researcher|Gather current public information from approved sources.
competitive-analyst|research|Competitive-Analyst|Compare products and market positioning.
trend-analyst|research|Trend-Analyst|Identify evidence-backed changes over time.
data-analyst|research|Data-Analyst|Clean, summarize, and analyze supplied datasets.
statistics-reviewer|research|Statistics-Reviewer|Check assumptions, uncertainty, and statistical claims.
visualization-designer|research|Visualization-Designer|Select clear charts for supported datasets.
document-analyst|research|Document-Analyst|Extract structure and key points from supplied files.
citation-manager|research|Citation-Manager|Maintain source attribution and claim-to-source links.
experiment-designer|research|Experiment-Designer|Propose measurable experiments and controls.
evaluation-scientist|research|Evaluation-Scientist|Build benchmarks and assess system quality.
business-analyst|business|Business-Analyst|Map processes, constraints, and opportunities.
project-manager|business|Project-Manager|Track scope, dependencies, risks, and delivery.
marketing-strategist|business|Marketing-Strategist|Propose evidence-based positioning and campaigns.
content-planner|business|Content-Planner|Create editorial plans from approved goals.
brand-editor|business|Brand-Editor|Maintain consistent voice and terminology.
customer-researcher|business|Customer-Researcher|Synthesize consented customer feedback.
support-triage|business|Support-Triage|Classify support requests and draft responses.
sales-researcher|business|Sales-Researcher|Prepare relevant public account research.
finance-analyst|business|Finance-Analyst|Analyze owner-provided budgets and financial data.
legal-document-assistant|business|Legal-Document-Assistant|Summarize supplied legal documents without legal determinations.
desktop-observer|desktop|Desktop-Observer|Analyze screen content only during explicit visible sessions.
window-organizer|desktop|Window-Organizer|Propose or perform approved window layout changes.
file-organizer|desktop|File-Organizer|Propose file moves and require approval for destructive changes.
local-search|desktop|Local-Search|Search user-selected folders within configured boundaries.
browser-assistant|desktop|Browser-Assistant|Read approved sites in an isolated visible browser session.
device-health|desktop|Device-Health|Report local device resource and health metrics.
storage-manager|desktop|Storage-Manager|Analyze disk usage and propose safe cleanup.
peripheral-assistant|desktop|Peripheral-Assistant|Inspect supported peripheral status and settings.
voice-assistant|desktop|Voice-Assistant|Handle explicit voice capture, transcription, and responses.
accessibility-assistant|desktop|Accessibility-Assistant|Recommend accessible interaction and presentation changes.
model-scout|ai-compute|Model-Scout|Discover candidate models and record license and hardware constraints.
model-evaluator|ai-compute|Model-Evaluator|Benchmark models on representative approved tasks.
model-router|ai-compute|Model-Router|Select providers by capability, privacy, latency, and cost.
prompt-engineer|ai-compute|Prompt-Engineer|Improve prompts using repeatable evaluations.
rag-engineer|ai-compute|RAG-Engineer|Tune retrieval, chunking, and grounded answer behavior.
inference-optimizer|ai-compute|Inference-Optimizer|Profile quantization, batching, and inference settings.
gpu-advisor|ai-compute|GPU-Advisor|Assess compatible GPU and CUDA acceleration options.
accelerator-researcher|ai-compute|Accelerator-Researcher|Compare GPU, TPU, and other accelerator trade-offs.
fine-tuning-analyst|ai-compute|Fine-Tuning-Analyst|Assess whether fine-tuning is justified and safe.
model-change-monitor|ai-compute|Model-Change-Monitor|Track upstream model changes and regression risk.
image-creator|creative|Image-Creator|Prepare image-generation briefs and approved requests.
presentation-designer|creative|Presentation-Designer|Structure slides and supporting narratives.
diagram-architect|creative|Diagram-Architect|Create architecture and process diagram specifications.
prototype-builder|creative|Prototype-Builder|Produce isolated, reviewable prototypes.
workflow-designer|creative|Workflow-Designer|Model repeatable workflows with explicit approval steps.
automation-reviewer|creative|Automation-Reviewer|Assess automation safety, reversibility, and idempotency.
travel-planner|creative|Travel-Planner|Research itineraries and present source-backed options.
wellbeing-organizer|creative|Wellbeing-Organizer|Organize user-entered habits without medical diagnosis.
emergency-information-guide|creative|Emergency-Information-Guide|Surface public emergency guidance without controlling devices.
capability-researcher|creative|Capability-Researcher|Investigate capabilities and recommend evidence-backed additions.
""".strip()


def _load_rows() -> tuple[Capability, ...]:
    items = []
    for line_number, line in enumerate(_ROWS.splitlines(), start=1):
        fields = line.split("|", 3)
        if len(fields) != 4:
            raise RuntimeError(f"Invalid capability catalog row {line_number}")
        capability_id, domain, name, description = (field.strip() for field in fields)
        if not capability_id or not domain or not name or not description:
            raise RuntimeError(f"Empty capability catalog field on row {line_number}")
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", capability_id):
            raise RuntimeError(f"Invalid capability ID: {capability_id}")
        items.append(Capability(capability_id, name, domain, description))
    ids = [item.capability_id for item in items]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Capability IDs must be unique")
    if len(items) != 100:
        raise RuntimeError(f"Expected 100 capabilities, found {len(items)}")
    return tuple(items)


CAPABILITIES = _load_rows()
_BY_ID = {item.capability_id: item for item in CAPABILITIES}


def get_capability(capability_id: str) -> Capability | None:
    """Return a catalog entry by its stable ID; this does not activate it."""
    if not isinstance(capability_id, str):
        return None
    return _BY_ID.get(capability_id.strip().lower())


def search_capabilities(query: str, *, limit: int = 10) -> tuple[Capability, ...]:
    """Deterministic lexical discovery; results are proposals, never dispatches."""
    if not isinstance(query, str) or not query.strip():
        return ()
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    terms = tuple(dict.fromkeys(re.findall(r"[a-z0-9]+", query.lower())))
    if not terms:
        return ()
    scored = []
    for item in CAPABILITIES:
        haystack = f"{item.capability_id} {item.name} {item.domain} {item.description}".lower()
        score = sum(1 for term in terms if term in haystack)
        if score:
            scored.append((-score, item.capability_id, item))
    scored.sort(key=lambda entry: (entry[0], entry[1]))
    return tuple(entry[2] for entry in scored[:limit])


def catalog_summary() -> dict[str, object]:
    """Return counts by domain and readiness state."""
    domains: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for item in CAPABILITIES:
        domains[item.domain] = domains.get(item.domain, 0) + 1
        statuses[item.status] = statuses.get(item.status, 0) + 1
    return {"total": len(CAPABILITIES), "domains": dict(sorted(domains.items())),
            "statuses": dict(sorted(statuses.items()))}
