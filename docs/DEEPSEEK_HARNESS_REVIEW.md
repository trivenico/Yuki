# DeepSeek Harness lessons for Yuki

Research checked: 17 October 2026. This document distinguishes observations from the public DeepSeek Harness repository from recommendations for Yuki; it does not claim that Yuki uses DeepSeek internals.

## What was reviewed

- DeepSeek Harness repository: https://github.com/deepseek-ai/deepseek-harness
- Architecture guidance: https://github.com/deepseek-ai/deepseek-harness/blob/master/AGENTS.md
- Tool registry and execution pipeline: https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/README.md
- Agent loop and durable session history: https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.md
- Workflow orchestration: https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/workflow/workflow/README.md

DeepSeek Harness is an agent harness, distinct from the DeepSeek-R1/V3 model projects. Its public README describes an everything-is-a-plugin architecture and identifies the project as a rapidly changing developer preview. Do not treat the preview status or its security notice as a production guarantee.

## Patterns worth adapting

### 1. Typed tool contracts
The Harness tool package describes typed input/output schemas, tool visibility/restrictions, allow/deny/ask policy, cooperative timeouts, and parallel-safety declarations.

**Yuki adaptation:** define a versioned SkillManifest with stable ID/version, JSON-schema input and output, declared capabilities, risk class, timeout/resource budget, dependencies, and idempotency semantics. Validate the manifest and arguments before dispatch. Expose only skills authorized for the current agent/session. A manifest is metadata, not a sandbox.

### 2. Separate the agent loop from tool execution
The Harness agent-loop documentation separates model turns, tool execution, streamed output, and durable session history. Ordinary tool failures can be returned as finalized tool results rather than corrupting the whole turn.

**Yuki adaptation:** make the task planner propose typed operations; a dispatcher validates policy and approval, invokes a bounded executor, records a safe observation, and resumes the task. Tool errors should have stable codes and bounded sanitized messages. Do not let model-generated text directly become executable code or shell commands.

### 3. Durable sessions and cancellation
The public agent-loop docs describe resuming persisted sessions, durable history, concurrency limits, ordering for exclusive calls, and cancellation that preserves already-streamed output. Repository guidance also calls out monotonic SQLite schema versions and cautious persistence migrations.

**Yuki adaptation:** keep task state and event history durable; use explicit schema migrations; recover interrupted jobs only through bounded/idempotent policies; propagate cancellation to cooperative handlers; use leases/heartbeats if multiple workers are introduced. The new core/durable_jobs.py is only a storage primitive and is not yet an integrated scheduler.

### 4. Explicit concurrency semantics
The Harness docs distinguish parallel-safe calls from exclusive calls and limit parallel tool calls.

**Yuki adaptation:** add parallel_safe and resource_group metadata to manifests, default to serial execution, and permit concurrency only for reviewed read-only operations. File writes, credential changes, messaging sends, and other side effects should be exclusive and approval-gated.

### 5. Plugin architecture without trusting plugins
A plugin-oriented design improves extensibility but also expands the attack surface. Python import-based plugins still run with the host process's privileges.

**Yuki adaptation:** do not port an unrestricted plugin loader. Start with declarative manifests and an isolated worker protocol. Use OS/container sandboxing, deny-by-default network, filesystem mounts, process/memory/CPU limits, and narrowly scoped brokered capabilities. If isolation is unavailable, only run built-in or explicitly trusted first-party plugins.

### 6. Preview discipline and migration discipline
The Harness repository warns that APIs can change incompatibly and documents persistence migration rules.

**Yuki adaptation:** version public plugin/task APIs, add schema migrations, test upgrade/rollback paths, publish compatibility notes, and gate releases on reproducible CI. Avoid importing the entire Harness or mirroring fast-moving internals without a concrete need.

## Proposed Yuki architecture

    Provider adapter
        |
    Agent planner (untrusted proposal)
        |
    Typed request validator
        |
    Policy + fresh approval bound to operation/target
        |
    Job store / lifecycle
        |
    Isolated worker or built-in capability
        |
    Bounded result validator + OutputGuard
        |
    Durable event history + redacted telemetry
        |
    User-facing response

## Adoption order

1. Typed, versioned skill manifests and stable error/result envelopes.
2. Operation-bound approvals (canonical action, target, arguments hash, expiry, one-time nonce, actor/session).
3. Integrate SQLite job storage with a worker, CLI/API status, cancellation, shutdown, and recovery.
4. Add isolated plugin workers and brokered filesystem/network capabilities.
5. Add safe concurrency declarations and per-resource limits.
6. Add migration tests, integration fixtures, and measured end-to-end evaluations.

## Do not copy blindly

- Do not assume a developer-preview project is production-ready just because it has a sophisticated architecture.
- Do not equate DeepSeek model capabilities with harness capabilities; DeepSeek-R1/V3 are model/inference projects, while DeepSeek Harness is orchestration software.
- Do not copy a plugin architecture without its validation, permissions, lifecycle, and isolation controls.
- Do not advertise performance or quality superiority without reproducible benchmark evidence.
