# OpenCode capability assessment and Yuki integration plan

## Executive summary

OpenCode is a useful reference for the interactive coding-agent product surface: configurable agents, granular tool permissions, custom tools, lifecycle plugins, provider/model selection, LSP-backed code intelligence, formatter integration, durable sessions, context compaction, and workspace snapshots/undo.

Yuki already has substantial equivalents for agent coordination, bounded workflows, approval gates, registered skills/tools, provider adapters, and durable task state. The correct strategy is selective integration into existing Yuki services, not a parallel OpenCode-style runtime and not a wholesale fork.

Reference documentation:
- https://github.com/anomalyco/opencode
- https://docs.opencode.ai/docs/agents/
- https://docs.opencode.ai/docs/tools/
- https://docs.opencode.ai/docs/plugins/
- https://docs.opencode.ai/docs/providers/
- https://docs.opencode.ai/docs/lsp/
- https://docs.opencode.ai/docs/formatters/
- https://docs.opencode.ai/docs/config/
- https://docs.opencode.ai/docs/troubleshooting/

## Existing Yuki capabilities verified on main

| Capability | Existing Yuki implementation | Assessment |
|---|---|---|
| Agent roles and delegation | \`automation/agent_coordinator.py\`, specialist registry, bounded dependency plans | Extend; avoid duplicate coordinator |
| Durable tasks/workflows | \`automation/task_runtime.py\`, \`automation/workflow_store.py\`, \`automation/workflow_engine.py\` | Extend with session-facing UX and recovery |
| Human approvals | \`core/permissions.py\`, \`automation/workflow_approval.py\`, guarded execution paths | Strengthen into centralized action/resource policy |
| Tool registry/dispatch | \`core/guarded_tool_runtime.py\`, \`yuki_tools.py\`, skills registry/catalog | Add schemas, metadata, versioning and plugin lifecycle |
| Plugins | \`core/plugin_loader.py\`, MCP skill integration | Current Python loader is trusted-code-only, not a sandbox; design a constrained extension contract |
| Model providers | \`providers/base.py\`, factory, catalog, capabilities, Ollama, OpenAI-compatible, Gemini CLI, hybrid | Add adapter coverage and capability conformance; do not claim 75+ support |
| Coding execution | \`core/coding_task_runner.py\`, \`core/coding_task_execution.py\`, Phase 93 engineering workflow | Integrate LSP/formatter and strengthen workspace recovery |
| Durable conversations | Task/workflow events exist; no complete user-facing agent session abstraction was found in the inspected tree | New capability |
| Workspace snapshots/undo | No dedicated session snapshot/undo service found in the inspected tree | New capability; prioritize |
| LSP integration | No dedicated LSP client/service found in the inspected tree | New capability |
| Formatter pipeline | No dedicated formatter registry/pipeline found in the inspected tree | New capability |
| Context compaction | No dedicated session context compaction service found in the inspected tree | New capability |

This is a source-tree inspection of the current main branch, not a claim that no experimental or untracked implementation exists elsewhere.

## Recommended design decisions

### 1. Reuse Yuki's agent and workflow runtime

Map OpenCode's Build, Plan, Explore and General patterns to Yuki agent profiles:
- Build: coding agent, with write/test permissions only through existing approvals.
- Plan: read-only analysis and planning.
- Explore: read-only repository navigation and search.
- Review: read-only diff, test and security analysis.
- Debug: bounded diagnostics and test reproduction.
- Specialist agents: route through the existing coordinator and TaskRuntime.

Agent profile configuration must not grant permissions. The central policy engine remains authoritative.

### 2. Central capability and permission policy

Introduce a versioned policy model with:
- principal, agent, tenant and workspace identity;
- action and resource scopes;
- allow / deny / approval-required decisions;
- risk classification and policy provenance;
- short-lived, action-digest-bound approvals;
- audit events and policy version;
- default deny for unknown tools and external directories.

Tool metadata and agent profiles are discoverability/configuration only, never authority. Tool execution must re-authorize at dispatch time.

### 3. Typed Tool Registry and extension contract

Evolve the existing registered tool/skill model with:
- JSON-schema input/output contracts;
- stable tool IDs, versions and capability declarations;
- execution timeout and output limits;
- side-effect classification;
- required permissions;
- lifecycle hooks around execution;
- trusted in-process extensions separated from isolated external extensions.

Do not execute arbitrary third-party plugin code in the Yuki process. Start with declarative manifests and subprocess/MCP isolation, signed packages, explicit allowlists, and per-extension permissions.

### 4. Durable Agent Sessions

Add a session aggregate above the existing task/workflow stores:
- durable user/agent messages and tool-call projections;
- append-only event sequence and replay cursor;
- parent/child session relationships;
- selected agent/model and workspace references;
- pending approvals and resumable input;
- idempotent message admission;
- bounded context summaries and tool-output pruning.

Keep task execution lifecycle separate from conversation history. A session wake notification is not crash recovery; use durable execution claims and bounded retry accounting.

### 5. Workspace snapshots and undo/redo

Create a workspace snapshot service before expanding autonomous editing:
- capture a baseline before every approved write batch;
- record content hashes, path, file mode and change metadata;
- reject path traversal and symlink escapes;
- stage writes and atomically replace files where supported;
- verify post-write hashes;
- support undo/redo as new audited transactions;
- protect secrets and large/generated directories by policy;
- retain bounded snapshots with explicit cleanup.

Do not rely on a second nested Git repository as the only recovery mechanism. Support normal Git worktrees/commits and content-addressed file backups.

### 6. LSP Code Intelligence

Build a provider-neutral LSP client service with a small initial operation set:
- initialize/shutdown and process health;
- document open/change/close;
- definition, references, hover and document symbols;
- diagnostics and workspace symbols;
- bounded request timeout, output and process count.

Launch servers only from an administrator-approved command registry. Never construct shell command strings from model output. LSP responses are untrusted context, not instructions.

### 7. Formatter and validation pipeline

Add a declarative formatter registry keyed by language/extensions:
- command argument arrays, never shell strings;
- explicit binary allowlist and environment;
- workspace-contained file paths;
- timeout, output caps and exit status;
- before/after content hashes;
- formatting inside the same rollback boundary as tests;
- formatter configuration from trusted project policy, not arbitrary repository instructions.

### 8. Provider/model gateway

Keep the existing provider abstraction. Add:
- normalized capabilities (streaming, tools, JSON schema, vision, embeddings);
- provider/model health and readiness probes;
- task-aware routing with cost, latency, privacy and capability constraints;
- credential isolation and secret redaction;
- conformance tests and deterministic fallback;
- local-first routing option for private repository context.

Provider support must be measured by tested adapter conformance, not catalog size.

### 9. Configuration and project onboarding

Adopt layered configuration concepts (global, workspace, project) with explicit precedence and schema validation. Project instructions such as AGENTS.md are untrusted repository content: parse as guidance but never let them override system policy or permissions. Add a project initialization command that generates a reviewable repository map and proposed project guide without silently modifying trusted policy.

## Implementation sequence

| Phase | Deliverable | Exit criteria |
|---|---|---|
| A | Integration inventory and architecture decision | Existing components mapped; no duplicate runtime |
| B | Central policy v2 and typed tool metadata | Default-deny, digest-bound approvals, audit and adversarial tests |
| C | Workspace snapshot/undo service | Atomic write, rollback, undo/redo and crash-recovery tests |
| D | Durable Agent Sessions | Idempotent admission, replay, compaction and bounded recovery |
| E | LSP service | Isolated server lifecycle and safe navigation/diagnostics tools |
| F | Formatter pipeline | Allow-listed formatters inside transactional validation |
| G | Extension SDK | Versioned manifests, isolated execution, lifecycle hooks and revocation |
| H | Provider capability conformance | Tested capability matrix and privacy-aware routing |
| I | CLI/Desktop integration | Shared session and workspace contracts across clients |

## Explicit non-goals

- Do not copy OpenCode wholesale or fork its runtime.
- Do not give every agent unrestricted shell or filesystem access.
- Do not treat plugin loading as a security sandbox.
- Do not let repository instructions grant tools or permissions.
- Do not claim exactly-once execution for external side effects.
- Do not add a second competing task/session database without a migration plan.

## Acceptance criteria for each phase

Every phase requires unit and integration tests, security tests for authorization boundaries, migration/rollback strategy, bounded resource use, structured audit events, and documentation. No phase is production-ready merely because a PR exists or CI is green.
