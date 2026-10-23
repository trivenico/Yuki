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
        "medium", ("tests/test_registry.py",),
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
        "high", ("tests/test_agent.py",),
        ("Not a mature native tool-calling/ReAct loop; provider and skill coverage varies.",),
    ),
    CapabilityContract(
        "local-memory", "Local notes and lexical memory search", "experimental",
        "Stores user-approved notes and supports local lexical retrieval.",
        ("core/vector_memory.py", "yuki.py"), ("local workspace storage",),
        ("memory:read", "memory:write", "memory:delete"),
        ("local memory persistence",), ("local",), "medium",
        ("tests/test_vector_memory.py",),
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
        ("tests/test_web_skill.py",),
        ("Not general browser control; retrieved page text remains untrusted.",),
    ),
    CapabilityContract(
        "mcp-runtime", "Restricted MCP client primitive", "experimental",
        "Provides an allow-listed HTTPS MCP client primitive with per-call approval.",
        ("core/mcp_client.py",), ("configured MCP servers",),
        ("mcp:discover", "mcp:call"), ("outbound HTTPS MCP requests",),
        ("local", "hosted"), "high", ("tests/test_mcp_client.py",),
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
        "specialist-orchestration", "Bounded specialist-agent orchestration", "planned",
        "Delegates work to bounded specialists under one coordinator and shared task context.",
        ("yuki_agent.py", "automation/workflow_engine.py"), ("durable task coordinator",),
        ("agent:delegate",), ("subtask execution and artifact creation",),
        ("local", "hosted"), "high", (),
        ("No complete durable specialist orchestration service is verified.",),
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
