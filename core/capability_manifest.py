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
        "persistent-conversation-sessions", "Workspace-scoped persistent conversations", "experimental",
        "Persists conversation sessions and ordered messages in SQLite, with workspace-scoped access, "
        "idempotent message appends, atomic single-active-turn lifecycle, bounded turn history, read-only archival, and persistent CLI run/resume.",
        ("core/conversation_store.py", "yuki_cli.py", "core/agent_cli_protocol.py"),
        ("SQLite", "local YUKI_HOME storage"),
        ("conversation:read", "conversation:write", "conversation:archive"),
        ("local conversation database writes",),
        ("local",), "high",
        ("tests/test_conversation_store.py", "tests/test_yuki_cli.py"),
        ("Workspace identity is supplied by the local CLI caller; hosted identity middleware is not implemented.",
         "Persistent turns currently call the configured provider directly with tool execution disabled; full guarded agent/tool runtime integration and desktop UI remain future work.",
         "Approvals and tool execution permissions are never persisted or granted by conversation records.",
         "No encryption-at-rest, multi-device synchronization, retention policy, or stale-turn recovery worker yet."),
    ),
    CapabilityContract(
        "agent-cli-ndjson", "Agent-native NDJSON CLI", "experimental",
        "Runs a single provider-backed Yuki turn and streams bounded JSON events with session and request correlation IDs.",
        ("yuki_cli.py", "core/agent_cli_protocol.py"),
        ("configured model provider",), ("provider:invoke",),
        ("outbound model-provider request",), ("local",),
        "medium", ("tests/test_agent_cli_protocol.py", "tests/test_yuki_cli.py"),
        ("Single-turn only; no session resume or conversation persistence.",
         "Tool execution is disabled in this CLI path; events are not tool authorization.",
         "Streaming currently chunks a completed provider response rather than forwarding provider token deltas."),
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
        "isolated-coding-execution", "Approval-gated Docker coding-task execution", "experimental",
        "Executes reviewed coding-task checks in a resource-bounded Docker container with immutable "
        "image digests, no network, restricted filesystem, non-root identity, and hash-only evidence.",
        ("core/coding_task_runner.py", "core/coding_task_execution.py",
         "core/sandbox_policy.py", "core/sandbox_runtime.py"),
        ("Docker Engine", "trusted digest-pinned image", "SQLite evidence store"),
        ("coding-task:execute", "coding-task:approve"),
        ("bounded container execution", "execution evidence persistence"),
        ("local",), "critical",
        ("tests/test_coding_task_runner.py", "tests/test_coding_task_execution.py",
         "tests/test_sandbox_runtime.py", "tests/test_sandbox_policy.py"),
        ("Experimental; requires a trusted Docker daemon and reviewed immutable image digest.",
         "The sandbox backend currently requires Unix UID/GID support; native Windows execution is not supported.",
         "Not integrated as the universal executor for specialist agents, skills, plugins, or desktop automation.",
         "No real-Docker adversarial integration test or production image provenance pipeline is included."),
    ),
    CapabilityContract(
        "independent-python-review", "Deterministic read-only Python change reviewer", "experimental",
        "Scans bounded proposed Python file contents for selected unsafe execution, deserialization, "
        "TLS, dynamic import, syntax, and credential patterns without executing the changes.",
        ("core/independent_code_review.py", "core/coding_task_review.py"),
        ("Python standard library", "CodingTaskStore"),
        ("review:read-proposed-source", "coding-task:review"),
        ("structured findings bound to patch digest and task version",),
        ("local", "hosted"), "high",
        ("tests/test_independent_code_review.py", "tests/test_coding_task_review.py"),
        ("Static heuristics are incomplete and can produce false positives and false negatives.",
         "Only Python source is analyzed; non-Python files are skipped and prevent a clean approval signal.",
         "Not an LLM reviewer, type checker, dependency scanner, test runner, or security certification.",
         "A clean report is not merge authorization; trusted human and CI approval remain mandatory."),
    ),
    CapabilityContract(
        "third-party-skill-import", "Pinned third-party declarative skill import", "experimental",
        "Imports selected SKILL.md instructions from pinned GitHub revisions or local files, "
        "shows lint and provenance metadata, classifies imported skills as medium or high risk, "
        "and preserves guarded invocation approval.",
        ("core/extension_store.py", "core/skill_linter.py", "yuki_cli.py"),
        ("PyYAML", "GitHub REST API for remote imports"),
        ("skills:import", "skills:invoke", "skills:approve"),
        ("local extension manifest writes", "bounded outbound GitHub API reads"),
        ("local",), "high",
        ("tests/test_extension_store.py", "tests/test_skill_linter.py",
         "tests/test_approved_skill_runtime.py"),
        ("Risk classification is heuristic and cannot establish that a skill is safe.",
         "Only SKILL.md is imported; companion scripts and reference assets are not installed.",
         "Operator must review exact pinned source and license before approval.",
         "License identifier/path are recorded, but full license text is not bundled for redistribution.",
         "Not a bulk marketplace, signature verification service, or universal skill runtime."),
    ),
    CapabilityContract(
        "isolated-skill-environment-planning", "Offline hash-locked skill environment planning", "experimental",
        "Validates pinned package lock entries and generates per-skill virtual-environment and offline "
        "installation command plans without creating environments, launching processes, or accessing networks.",
        ("core/skill_environment_manager.py", "core/skill_dependency_manager.py"),
        ("Python standard library",),
        ("skills:read", "dependencies:inspect", "environments:plan"),
        ("metadata validation", "deterministic lockfile rendering", "offline command-plan generation"),
        ("local",), "high",
        ("tests/test_skill_environment_manager.py",),
        ("No environment is created and no command is executed; a separate approved executor is required.",
         "The lockfile format is a deliberately narrow internal subset, not a complete pip requirements parser.",
         "Every direct and transitive package must be pinned and hash-locked by a trusted lock-generation process.",
         "Wheel provenance, signatures, malware scanning, and artifact attestation are not implemented.",
         "No CLI or desktop environment lifecycle commands are wired yet."),
    ),
    CapabilityContract(
        "skill-dependency-planning", "Read-only skill dependency and compatibility planning", "experimental",
        "Validates bounded skill and Python package constraints, resolves installed skill dependency "
        "closures, and reports missing, incompatible, or cyclic dependencies without installing packages.",
        ("core/skill_dependency_manager.py", "core/extension_store.py"),
        ("Python standard library", "installed extension manifests"),
        ("skills:read", "dependencies:inspect"),
        ("metadata-only dependency plan generation",),
        ("local",), "medium",
        ("tests/test_skill_dependency_manager.py", "tests/test_extension_store.py"),
        ("No package installation, environment creation, lockfile generation, or CLI/desktop workflow is wired yet.",
         "Version constraints support numeric versions and a deliberately limited operator subset, not full PEP 440.",
         "The planner checks currently installed packages; it does not resolve a package index or prove transitive Python package compatibility.",
         "Skill dependencies are resolved only against already installed declarative skill manifests."),
    ),
    CapabilityContract(
        "confidence-aware-skill-routing", "Confidence-aware on-demand skill discovery", "experimental",
        "Ranks registered skill metadata deterministically, exposes match evidence, and refuses weak "
        "or ambiguous task-to-skill selections before requesting invocation approval.",
        ("skills/catalog.py", "core/agent.py"),
        ("registered skill metadata", "Python standard library"),
        ("skill:discover", "skill:invoke"),
        ("metadata-only registry reads", "explicitly approved skill invocation"),
        ("local", "hosted"), "high",
        ("tests/test_skill_catalog.py", "tests/test_agent_skill_catalog_integration.py"),
        ("Lexical ranking is not semantic retrieval and may miss paraphrases or multilingual queries.",
         "Confidence thresholds require evaluation against a representative task/skill benchmark.",
         "Discovery does not authorize execution; PermissionGate and guarded invocation remain mandatory.",
         "CLI/provider task loops and specialist workers are not yet integrated with this selector."),
    ),
    CapabilityContract(
        "typed-tool-execution", "Typed fail-closed agent tool execution", "experimental",
        "Validates structured tool calls against registered schemas, checks policy on every call, "
        "requires exact-call approval for side-effecting or high-risk tools, and bounds results.",
        ("core/typed_tool_runtime.py", "core/task_contracts.py"),
        ("Python standard library",),
        ("tool:invoke", "tool:policy:authorize", "tool:approval:exact-call"),
        ("registered handler invocation", "metadata-only audit events"),
        ("local",), "high",
        ("tests/test_typed_tool_runtime.py", "tests/test_task_contracts.py"),
        ("In-process execution is not a sandbox; registered handlers retain process privileges.",
         "Timeouts and process isolation must be supplied by a future isolated executor.",
         "The runtime is not yet wired as the single dispatch path for every CLI, desktop, plugin, and worker call site."),
    ),
    CapabilityContract(
        "specialist-orchestration", "Bounded specialist-agent orchestration", "experimental",
        "Durably plans and routes bounded specialist subtasks, enforces dependencies and approvals, "
        "and executes registered text-only handlers with task leases and recovery.",
        ("automation/agent_coordinator.py", "automation/specialist_worker.py",
         "automation/task_runtime.py", "automation/workflow_store.py"),
        ("SQLite", "configured model provider"),
        ("agent:plan", "agent:dispatch", "agent:read", "task:create", "task:complete",
         "task:lease:renew", "task:recover"),
        ("task and plan persistence", "specialist text generation", "task event journaling"),
        ("local",), "high",
        ("tests/test_agent_coordinator.py", "tests/test_specialist_worker.py",
         "tests/test_task_runtime.py", "tests/test_workflow_store.py"),
        ("Specialist actions are limited to registered emit handlers; generated code is not executed.",
         "Worker execution is not yet isolated in a hardened process/container.",
         "Hosted multi-tenant orchestration and production scheduler/observability are not implemented.",
         "This is an experimental bounded runtime, not a verified 100-agent production service."),
    ),
    CapabilityContract(
        "hosted-application-auth", "Hosted HTTP authentication integration", "planned",
        "Connects account/session service to trusted HTTP middleware and transport controls.",
        ("core/auth.py",), ("HTTP application framework", "TLS termination"),
        ("auth:authenticate", "auth:resolve-session"),
        ("session issuance and protected API requests",), ("hosted",), "critical", (),
        ("Phase 81 provides a service layer only; no HTTP middleware or hosted login UI exists.",),
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
