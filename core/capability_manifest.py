"""Evidence-backed capability contracts for Yuki.

A module's existence is not proof of an end-to-end feature. Keep statuses
conservative and attach concrete test evidence to every implemented claim.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


CAPABILITY_STATUSES = frozenset({"implemented", "experimental", "planned", "disabled"})
DEPLOYMENTS = frozenset({"local", "hosted"})
RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})


@dataclass(frozen=True)
class CapabilityContract:
    capability_id: str
    name: str
    status: str
    summary: str
    entrypoints: tuple[str, ...]
    dependencies: tuple[str, ...]
    permissions: tuple[str, ...]
    side_effects: tuple[str, ...]
    deployments: tuple[str, ...]
    risk: str
    test_evidence: tuple[str, ...]
    limitations: tuple[str, ...] = ()

    def validate(self) -> list[str]:
        errors = []
        if not self.capability_id or not self.capability_id.replace("-", "").replace("_", "").isalnum():
            errors.append("capability_id must be a non-empty simple identifier")
        if self.status not in CAPABILITY_STATUSES:
            errors.append(f"unsupported status: {self.status}")
        if self.risk not in RISK_LEVELS:
            errors.append(f"unsupported risk: {self.risk}")
        if not self.name.strip() or not self.summary.strip():
            errors.append("name and summary are required")
        if not self.entrypoints:
            errors.append("at least one entrypoint/module is required")
        if not self.deployments or not set(self.deployments) <= DEPLOYMENTS:
            errors.append("deployments must be a non-empty subset of local, hosted")
        if self.status == "implemented" and not self.test_evidence:
            errors.append("implemented capabilities require test evidence")
        if any(not isinstance(item, str) or not item.strip() for item in (
            self.entrypoints + self.dependencies + self.permissions +
            self.side_effects + self.deployments + self.test_evidence + self.limitations
        )):
            errors.append("contract list fields must contain non-empty strings")
        return errors


# Conservative statuses reflect README/docs, not the number of source files.
# "implemented" is reserved for a narrow contract with a named regression test.
DEFAULT_CAPABILITIES: tuple[CapabilityContract, ...] = (
    CapabilityContract(
        "skill-registry", "Validated skill registry", "implemented",
        "Registers uniquely named skills and describes them without executing them.",
        ("skills/registry.py",), ("Python 3.10+",), ("skill:invoke",),
        ("registered skill execution when explicitly invoked",), ("local", "hosted"),
        "medium", ("tests/test_role_plugin_loader.py",),
        ("Registry presence does not prove each skill is complete or production-ready.",),
    ),
    CapabilityContract(
        "workflow-approval", "Durable workflow approval boundary", "implemented",
        "Workflow approvals bind to a trusted principal and approval request digest.",
        ("automation/workflow_approval.py", "automation/workflow_store.py"),
        ("SQLite",), ("workflow:approve",), ("workflow state transitions",),
        ("local", "hosted"), "high",
        ("tests/test_workflow_approval.py", "tests/test_workflow_store.py"),
        ("Transport middleware must provide a trusted identity; no hosted HTTP middleware is included.",),
    ),
    CapabilityContract(
        "built-in-auth", "Built-in account and session service", "implemented",
        "Password hashes, opaque sessions, revocation, lockout and least-privilege roles.",
        ("core/auth.py",), ("Python standard library", "SQLite"),
        ("auth:authenticate", "auth:resolve-session"),
        ("account/session database writes",), ("local", "hosted"), "high",
        ("tests/test_auth.py",),
        ("No HTTP server, login UI, MFA, password reset, or hosted transport middleware.",),
    ),
    CapabilityContract(
        "bounded-agent-runtime", "Bounded model-guided task runtime", "experimental",
        "JSON-based task proposals and guarded registered-skill invocation.",
        ("yuki.py", "yuki_agent.py"), ("configured model provider",),
        ("skill:invoke",), ("approved skill actions",), ("local",),
        "high", ("tests/test_yuki_agent_runtime.py",),
        ("Not a mature native tool-calling/ReAct loop; provider and skill coverage varies.",),
    ),
    CapabilityContract(
        "local-memory", "Local notes and lexical memory search", "experimental",
        "Stores user-approved notes and supports local lexical retrieval.",
        ("core/vector_memory.py", "yuki.py"), ("local workspace storage",),
        ("memory:read", "memory:write", "memory:delete"),
        ("local memory persistence",), ("local",), "medium",
        ("tests/test_yuki.py",),
        ("Not an embedding/vector database or tenant-aware hosted memory service.",),
    ),
    CapabilityContract(
        "workflow-runner", "Bounded allow-listed workflow runner", "experimental",
        "Runs bounded steps through an allow-listed action runner with approval gates.",
        ("automation/workflow_engine.py",), ("SQLite workflow store",),
        ("workflow:run", "workflow:approve"), ("workflow state and approved step effects",),
        ("local", "hosted"), "high",
        ("tests/test_workflow_engine.py",),
        ("No durable scheduler, webhooks, or background worker service.",),
    ),
    CapabilityContract(
        "web-research", "Allow-listed web page reading", "experimental",
        "Reads bounded page text from explicitly allowed HTTPS hosts.",
        ("skills/web_skill.py", "skills/web_deep_read.py", "automation/browser.py"),
        ("configured allowed-host list",), ("web:read",),
        ("outbound HTTPS requests",), ("local", "hosted"), "medium",
        ("tests/test_production_integrations.py",),
        ("Not general browser control; retrieved page text remains untrusted.",),
    ),
    CapabilityContract(
        "mcp-runtime", "Restricted MCP client primitive", "experimental",
        "Provides an allow-listed HTTPS MCP client primitive with per-call approval.",
        ("core/mcp_client.py",), ("configured MCP servers",),
        ("mcp:discover", "mcp:call"), ("outbound HTTPS MCP requests",),
        ("local", "hosted"), "high", ("tests/test_production_integrations.py",),
        ("Not wired into the default runtime command/registry/UI; no stdio or SSE-only servers.",),
    ),
    CapabilityContract(
        "desktop-control", "General desktop computer control", "planned",
        "Permissioned observe-act-verify control of selected desktop applications.",
        ("companion/",), ("desktop automation adapter",),
        ("desktop:observe", "desktop:act"), ("desktop application interaction",),
        ("local",), "critical", (),
        ("No general verified desktop-control loop is currently implemented.",),
    ),
    CapabilityContract(
        "interruptible-voice", "Real-time interruptible voice conversation", "planned",
        "Streaming voice with interruption, visible recording state, and text fallback.",
        ("voice/stt.py", "voice/tts.py", "voice/wakeword.py"),
        ("compatible audio device", "local speech models"), ("voice:record", "voice:speak"),
        ("microphone capture and audio playback",), ("local",), "high", (),
        ("Finite recording and chunked playback exist; automatic barge-in is not wired end to end.",),
    ),
    CapabilityContract(
        "specialist-orchestration", "Bounded specialist-agent orchestration", "experimental",
        "Persists bounded specialist plans, routes by declared capabilities, and dispatches dependency-ready tasks.",
        ("automation/agent_coordinator.py", "automation/task_runtime.py"),
        ("SQLite workflow store", "trusted authorization callback",),
        ("agent:plan", "agent:dispatch", "agent:read"),
        ("durable plan and task metadata",), ("local",), "high",
        ("tests/test_agent_coordinator.py", "tests/test_task_runtime.py"),
        ("No hosted API, autonomous scheduler, distributed worker fleet, or cancellation propagation.",),
    ),
    CapabilityContract(
        "specialist-worker", "Provider-backed specialist task worker", "experimental",
        "Executes explicitly registered, bounded text-only specialist handlers through leased durable tasks.",
        ("automation/specialist_worker.py",),
        ("configured provider adapter", "SQLite task runtime",),
        ("task:read", "task:complete", "task:fail"),
        ("provider inference and durable task event writes",), ("local",), "high",
        ("tests/test_specialist_worker.py",),
        ("No shell, generated-code execution, workspace writes, or automatically managed scheduler.",),
    ),
    CapabilityContract(
        "specialist-read-tools", "Authorized read-only workspace evidence adapter", "experimental",
        "Allows specialists to consume explicitly declared workspace list, read, and search results.",
        ("automation/specialist_read_tool_adapter.py", "automation/authorized_read_tools.py"),
        ("trusted principal resolver", "authorized read-tool registry",),
        ("workspace:read",),
        ("workspace metadata and file content reads",), ("local",), "high",
        ("tests/test_specialist_read_tool_adapter.py",),
        ("No write, shell, process, network, or model-invented tool execution.",),
    ),
    CapabilityContract(
        "hosted-application-auth", "Hosted HTTP authentication integration", "planned",
        "Connects account/session service to trusted HTTP middleware and transport controls.",
        ("core/auth.py",), ("HTTP application framework", "TLS termination"),
        ("auth:authenticate", "auth:resolve-session"),
        ("session issuance and protected API requests",), ("hosted",), "critical", (),
        ("Phase 81 provides a service layer only; no HTTP middleware or hosted login UI exists.",),
    ),
    CapabilityContract(
        "provider-runtime", "Configured AI provider runtime", "experimental",
        "Constructs configured provider adapters and exposes provider/model selection metadata.",
        ("providers/factory.py", "providers/runtime_manager.py"),
        ("configured provider credentials or local inference server",),
        ("model:select", "inference:request"),
        ("outbound provider requests or local model inference",), ("local",), "high",
        ("tests/test_provider_factory.py", "tests/test_provider_runtime_manager.py"),
        ("A provider adapter being available does not prove quality, latency, fallback, or production reliability.",),
    ),
    CapabilityContract(
        "conversation-api", "Authenticated conversation application API", "experimental",
        "Composes authenticated conversation APIs, persistence and bounded model execution.",
        ("api/runtime.py", "api/conversations.py", "core/conversation_service.py"),
        ("configured provider", "SQLite or configured conversation store"),
        ("conversation:read", "conversation:write", "auth:session"),
        ("conversation persistence and provider inference",), ("local", "hosted"), "high",
        ("tests/test_conversation_api.py", "tests/test_conversation_service.py"),
        ("Web launcher tool policy is deny-all by default; deployment and full tool approval flow require separate verification.",),
    ),
    CapabilityContract(
        "companion-foundation", "Yuki companion identity and memory modules", "experimental",
        "Provides companion identity, persona, mood, proactive and memory primitives.",
        ("companion/identity_manager.py", "companion/persona.py", "companion/memory.py"),
        ("local companion state",), ("companion:read", "companion:write"),
        ("companion profile and memory state changes",), ("local",), "medium",
        ("tests/test_companion_skills_agent.py",),
        ("The module inventory does not prove a persistent companion loop, scheduler, notifications, or complete integration with every interface.",),
    ),
    CapabilityContract(
        "cli-assistant", "Yuki command-line chat and diagnostics", "experimental",
        "Provides doctor, provider status, one-shot ask and interactive text chat commands.",
        ("yuki_cli.py",), ("Python runtime", "configured provider"),
        ("inference:request",), ("provider inference and terminal output",), ("local",), "medium",
        ("tests/test_yuki_cli.py", "tests/test_provider_smoke_cli.py"),
        ("No versioned installable package, stable console entry point, shell completion, or complete auth/session lifecycle is established.",),
    ),
    CapabilityContract(
        "model-registry-evaluation", "AI model registry and evaluation primitives", "experimental",
        "Tracks model metadata and provides evaluation/scouting utilities for supported model workflows.",
        ("providers/model_registry.py", "providers/model_evaluation.py"),
        ("model metadata and evaluation datasets",), ("model:discover", "model:evaluate"),
        ("local evaluation compute and optional model/provider requests",), ("local",), "medium",
        ("tests/test_model_registry.py", "tests/test_model_evaluation.py"),
        ("No evidence of a Yuki-trained proprietary foundation model or a release-qualified model benchmark suite.",),
    ),
    CapabilityContract(
        "paired-device-inference", "Authenticated paired-device inference jobs", "experimental",
        "Provides owner-scoped device pairing and authenticated leased inference job lifecycle.",
        ("core/device_pairing.py", "core/inference_jobs.py", "api/inference.py"),
        ("SQLite", "paired device agent"), ("device:pair", "device:revoke", "inference:claim"),
        ("paired-device metadata and inference job state changes",), ("local",), "high",
        ("tests/test_inference_api.py", "tests/test_inference_jobs.py"),
        ("A production device agent distribution, attestation, transport and real-device matrix remain incomplete.",),
    ),
    CapabilityContract(
        "income-opportunity-pipeline", "Owner-scoped online income opportunity tracking", "experimental",
        "Tracks researched opportunities and owner-controlled lifecycle state without claiming earnings.",
        ("core/income_opportunities.py", "core/income_discovery.py", "api/income.py"),
        ("SQLite", "explicitly configured platform adapters"),
        ("income:read", "income:manage"),
        ("opportunity metadata and owner-approved lifecycle changes",), ("local", "hosted"), "high",
        ("tests/test_income_opportunities.py", "tests/test_income_discovery.py", "tests/test_income_api.py"),
        ("No live platform adapter, proposal submission, payment action, or verified revenue generation is implied.",),
    ),
    CapabilityContract(
        "native-desktop-application", "Installable Yuki desktop application", "planned",
        "A secure native desktop application with signed installers, OS credential storage, updates and recovery.",
        ("planned:desktop-shell",), ("desktop application framework", "shared application runtime"),
        ("desktop:launch", "desktop:notifications"),
        ("local application state and OS integration",), ("local",), "high", (),
        ("No native desktop application project or signed desktop release artifact is present in the audited repository.",),
    ),
    CapabilityContract(
        "end-to-end-voice", "Integrated speech conversation", "experimental",
        "Provides local speech recognition and synthesis adapters for voice workflows.",
        ("voice/stt.py", "voice/tts.py", "voice/wakeword.py"),
        ("compatible audio hardware", "configured speech models"),
        ("voice:record", "voice:transcribe", "voice:speak"),
        ("microphone capture, audio file access and playback",), ("local",), "high",
        ("tests/test_multilingual_voice.py",),
        ("An end-to-end interruptible voice conversation and always-on listener are not verified.",),
    ),

)


def capability_manifest() -> tuple[CapabilityContract, ...]:
    """Return the canonical manifest; callers should not mutate shared state."""
    return DEFAULT_CAPABILITIES


def validate_manifest(contracts: Iterable[CapabilityContract] | None = None,
                      repository_root: str | Path = ".") -> list[str]:
    root = Path(repository_root).resolve()
    entries = tuple(DEFAULT_CAPABILITIES if contracts is None else contracts)
    errors: list[str] = []
    seen: set[str] = set()
    for item in entries:
        prefix = item.capability_id or "<missing-id>"
        for error in item.validate():
            errors.append(f"{prefix}: {error}")
        if item.capability_id in seen:
            errors.append(f"{prefix}: duplicate capability_id")
        seen.add(item.capability_id)
        for evidence in item.test_evidence:
            path = Path(evidence)
            if path.is_absolute() or ".." in path.parts:
                errors.append(f"{prefix}: unsafe test evidence path {evidence!r}")
                continue
            if not (root / path).is_file():
                errors.append(f"{prefix}: test evidence file does not exist: {evidence}")
    return errors


def manifest_as_dicts() -> list[dict]:
    """JSON-friendly representation, with tuples serialized as lists."""
    result = []
    for contract in DEFAULT_CAPABILITIES:
        entry = asdict(contract)
        for key, value in entry.items():
            if isinstance(value, tuple):
                entry[key] = list(value)
        result.append(entry)
    return result
