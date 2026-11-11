# Phase 208 — Herdr research: agent workspace and runtime orchestration

## Research scope

Reviewed the public Herdr repository and current documentation on 2026-11-11: https://github.com/herdrdev/herdr. Sources included its README, concepts, agent detection/automation, socket API, agent skill, session persistence/restore, SSH remote attach, saved machines, plugin/marketplace documentation, Cargo manifest and source layout.

This is an architecture/product review, not a security audit, performance benchmark, source-code reuse, or independent verification of every advertised platform behavior. Herdr is Apache-2.0 licensed. This document records independently derived design lessons; it does not copy Herdr implementation code.

## What Herdr is

Herdr is a Rust terminal workspace/process manager for coding agents, not a foundation model, general-purpose autonomous agent, or replacement for agent runtimes. It owns real terminal processes and organizes them into workspaces, tabs and panes. It recognizes supported coding agents, observes lifecycle state, exposes CLI/socket automation, preserves a live server across client detach, restores layout after restart, and connects remote machines over SSH.

Its strongest product insight is the separation of:
1. Workspace topology — workspace, tab, pane and layout.
2. Process/terminal control — start, read, send input, wait for output, resize and close.
3. Agent lifecycle — identity, working/blocked/done/idle/unknown state, prompt, wait and resume.
4. Client/server lifecycle — a background owner of PTYs and multiple attached clients.
5. Remote machine transport — independent server per machine, SSH bridge and reconnect.
6. Extensions — integrations for agent-specific lifecycle/session identity and plugins for user workflows.

This separation is more valuable than copying a terminal UI. It gives automation stable primitives and prevents agent-specific assumptions from contaminating process management.

## Lessons worth adopting in Yuki

### 1. A first-class agent workspace runtime

Yuki already has a bounded model/tool execution loop, durable task runtime, specialist coordinator, worker supervisor, provider adapters, and a packaged CLI. What is missing is a cohesive process/workspace control plane that can host and observe multiple external agent processes.

Build a separate AgentWorkspaceRuntime with explicit domain objects:
- Workspace: project/task scope, root directory, labels and lifecycle.
- Tab: named view or work stage.
- Pane: one supervised process/PTY, working directory, dimensions and bounded output stream.
- AgentSession: recognized agent identity, provider/kind, native session reference, lifecycle state and evidence.
- Machine: local or explicitly enrolled remote execution host.
- ProcessLease: owner, process identity, generation, heartbeat and termination/recovery state.

Keep this runtime independent of Yuki's model/tool loop. Yuki's internal agents may use it to host reviewed workers; external tools (Claude Code, Codex, Gemini CLI, Kimi Code, OpenCode, Hermes Agent, etc.) should remain optional subprocess integrations, not hidden dependencies.

### 2. Separate process state from agent state

Adopt a normalized lifecycle contract:
- working: evidence of active work.
- blocked: explicit approval/question state detected with high confidence.
- done: a completed turn not yet acknowledged by the owner/client.
- idle: ready for input or waiting.
- unknown: insufficient or conflicting evidence.

Do not treat unknown as success. Do not equate idle with completed task. Keep completion sequence/turn identity separate from process state so a short task that starts and finishes between UI polls is not lost.

Use evidence provenance:
- Official agent hooks/events are authoritative only for the fields they explicitly report.
- Process/PTY state is authoritative for process existence and exit.
- Screen detection is heuristic and must report the matched rule, confidence/evidence and manifest version.
- Stale or conflicting sources resolve to unknown, not an optimistic state.

Critical safety rule: blocked/approval prompts must never be answered automatically just because a detector recognizes them. Detection is observation, not authorization. A mistaken idle state must not cause Yuki to type into an approval dialog.

### 3. CLI first, typed local socket second

Herdr's layering is effective:
- Stable JSON CLI for human operators and shell automation.
- Versioned local IPC/socket protocol for long-lived subscriptions and richer clients.
- Machine-readable JSON Schema bundled with the installed binary.
- Explicit protocol version and capability negotiation.
- Event subscriptions plus authoritative snapshot reconciliation after reconnect.

Yuki should standardize JSON envelopes, stable error codes, request IDs, bounded input/output, idempotency keys, and exit codes. A successful send_input should mean queue admission only—not that the command ran or succeeded. Long-running work should return a durable operation ID and expose wait, read, cancel, and status separately.

For event-driven clients: subscribe, fetch an authoritative snapshot, reconcile invalidations, and repeat the snapshot after reconnect. Do not treat an event stream as a replayable event log unless it actually has durable sequence/cursor semantics.

### 4. Persistent supervisor, explicit restore tiers

Herdr clearly distinguishes:
- Detach/reattach: original process continues in a live server.
- Server restart: layout and cwd restore, but arbitrary processes do not survive.
- Screen-history replay: optional and sensitive; terminal output may contain secrets.
- Native agent session restore: resume only when a supported agent integration has a valid session reference/resume command.
- Live process handoff: experimental, distinct from snapshot restore.

Yuki should document and implement the same explicit tiers rather than promising that a process survives restart. Store layout metadata separately from screen history and agent session references. Keep screen history off by default, bounded, encrypted where practical, access-controlled and subject to retention/deletion. Validate saved working directories and preserve recoverable state on corrupt/incompatible snapshots.

### 5. Agent integrations should be small adapters

Herdr's integration model suggests one adapter contract per external agent:
- Detect/install/uninstall integration with preview and owner consent.
- Report agent kind, native session ID and lifecycle events.
- Provide a validated resume command.
- Declare adapter version and supported capabilities.
- Uninstall only files/entries owned by that integration.
- Never assume a hook event implies the whole task is complete.

Yuki can reuse its provider/agent registry concepts, but add a separate process integration registry. Model providers, agent executables, process supervisors and UI plugins are different extension classes and should not share one unsafe generic plugin interface.

### 6. Multi-machine execution with explicit trust

Herdr's SSH model uses one server per machine, separate process ownership, independent reconnects and a local client that switches between machines. Yuki's same-LAN device-control ambition should borrow the separation, not blindly copy SSH assumptions.

For Yuki:
- Begin with explicit device enrollment and owner approval.
- Authenticate every device-to-device request using the enrolled identity.
- Bind requests to device ID, owner, capability, expiry, nonce and generation.
- Use mutual TLS or a comparably strong authenticated transport.
- Apply capability allowlists and approval gates on the receiving device.
- Use replay protection, rate limits, audit events, revocation and rotation.
- Reconnect read-only first and never replay a possibly side-effecting request blindly.
- Do not scan arbitrary subnets or silently access microphones, cameras, files or shells.

Remote execution should be an optional supervised worker host, not a remote arbitrary-shell backdoor.

### 7. Plugins: extension surface, not sandbox

Herdr's plugin model is intentionally transparent: plugin commands run as the current user and can invoke the full CLI. Its docs explicitly warn that plugins are not sandboxed or reviewed by the marketplace.

Yuki should make a stricter choice:
- Signed/pinned plugin artifacts and provenance.
- Declarative manifests and capability requests.
- Per-plugin permissions and isolated execution where possible.
- No inherited provider secrets by default.
- Owner confirmation before install, enable, update or high-impact invocation.
- Audit logs, revocation and rollback.
- Separate trusted first-party adapters from community plugins.

Do not describe a manifest validator as a sandbox. If a plugin can execute arbitrary code as the user, state that clearly and isolate it.

### 8. Operational quality matters more than a flashy multiplexer

Herdr's documentation is unusually explicit about failure modes: server/client separation, SSH authentication, host-key checking, reconnect backoff, stale cached state, process loss after restart, screen-history privacy, plugin trust, and the limits of live handoff.

Yuki should adopt that level of operational precision in its desktop/CLI docs and acceptance tests. In particular:
- Report cached vs live machine/agent state.
- Do not repeat a prompt or write after an ambiguous timeout.
- Do not replay tasks after lease expiry without fresh authorization.
- Distinguish request acceptance from execution completion.
- Make recovery and backup procedures operator-readable.

## Recommended Yuki implementation plan

### P0 — contracts and runtime boundary
1. Define AgentWorkspaceRuntime interfaces and versioned JSON protocol.
2. Define process, pane, agent-session and machine state models with provenance and generation/fencing.
3. Add read-only inventory/snapshot/events and CLI JSON output.
4. Add stable error codes, request IDs, operation IDs, bounded output and idempotent create/start.
5. Threat-model local IPC, process ownership, path handling, secret leakage and cross-user access.

### P1 — local supervised process support
1. Implement cross-platform PTY/process host abstraction.
2. Create/list/read/wait/resize/terminate panes with bounded output and process-tree cleanup.
3. Add workspace/tab layout persistence independent of processes.
4. Add crash-safe server lifecycle, single-instance ownership, graceful shutdown and startup recovery.
5. Test Linux, macOS and Windows process behavior; no production claim before real OS integration tests.

### P2 — external agent adapters
1. Start with one or two supported CLI agents and their documented hooks/session-resume APIs.
2. Keep screen-based detection opt-in and conservative; explain why a state was inferred.
3. Implement blocked-state observation without automatic approval or input.
4. Add resume only for validated native session IDs and documented commands.
5. Test agent upgrades, malformed hooks, stale session IDs, process replacement and prompt ambiguity.

### P3 — desktop experience and remote machines
1. Build a desktop client over the same local protocol; never create a second orchestration engine in the UI.
2. Add multiple clients with independent view state and a single authoritative server.
3. Add explicitly enrolled remote worker hosts after identity, transport, revocation and policy gates pass.
4. Add reconnect/backoff, health, host-key/identity warnings and no blind side-effect retries.

### P4 — governed extension ecosystem
1. Define distinct provider, agent-process, workflow and UI-plugin interfaces.
2. Add signed manifests, artifact pinning, capability permissions, sandboxing and revocation.
3. Publish an extension catalog only after provenance and trust controls exist.

## Explicit non-goals and constraints

- Do not copy Herdr source code or make Yuki a fork/rebrand. Implement independent interfaces from the product lessons.
- Do not replace Yuki's model runtime, durable task engine, approvals, memory or income-opportunity pipeline with a terminal manager.
- Do not treat terminal/process orchestration as the same thing as AI reasoning or model training.
- Do not enable arbitrary shell execution, agent-to-agent terminal input, plugin execution or remote control by default.
- No calling feature is proposed or reintroduced.
- No automatic approval, silent microphone/camera activation, or unrestricted LAN scanning.
- No promise that every external coding agent can be resumed; each adapter must prove its own contract.

## Current Yuki overlap and gaps

Already present in Yuki's repository:
- Bounded AgentExecutionLoop with tool authorization delegated to StructuredToolProtocol/GuardedToolRuntime.
- Durable task runtime, task event journal, leases and cooperative renewal.
- Bounded specialist coordinator with capability matching, dependency DAG, idempotent plan submission and fail-closed authorization.
- Finite-cycle worker supervisor with expired-lease interruption and no automatic replay.
- Provider/agent adapter foundations and packaged CLI with persistent conversation runtime.
- Ed25519 device identity enrollment work in a separate draft PR.

Still missing for a Herdr-like control plane:
- Real cross-platform PTY/process manager and workspace/tab/pane model.
- Local server daemon lifecycle and multi-client protocol.
- Process-level observability and terminal output streaming.
- External agent process lifecycle adapters and native session resume.
- Desktop client over the shared runtime.
- Remote machine transport and device host agent.
- End-to-end OS-level crash/restart, security and performance validation.

## Decision

Adopt the architectural patterns, not the product wholesale. Yuki should retain its existing AI/task/approval engine and add a distinct, secure Agent Workspace Runtime as the process and collaboration plane. This unlocks a genuine desktop experience and lets Yuki supervise both its own approved workers and third-party coding agents without conflating terminal state, task state, and model reasoning.

The first implementation should be a read-only local inventory/snapshot/event contract and threat model, followed by a cross-platform PTY process host. Do not start by building a full TUI or enabling remote shell control.
