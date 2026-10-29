# Third-party AI agent reuse strategy

**Status:** Accepted for evaluation; no new runtime dependency approved yet  
**Owner:** Yuki core runtime  
**Last reviewed:** 2026-10-29

## Decision

Yuki may reuse and adapt permissively licensed open-source code when it removes meaningful engineering work, but must not wholesale-copy an agent repository into the core runtime. Prefer, in order:

1. Reuse interoperable protocols and formats already supported by Yuki (Agent Skills, MCP, OpenAI-compatible model APIs).
2. Integrate a mature project behind a narrow, optional adapter or subprocess boundary.
3. Port a small, well-isolated implementation only after license, provenance, security, and maintenance review.
4. Implement in-house only where Yuki's privacy model, permission checks, cross-platform packaging, or companion UX require it.

No third-party agent may bypass Yuki's authorization, workspace restrictions, audit trail, tool contracts, model-routing policy, or human approvals. A framework's own approval mechanism is not a substitute for Yuki's authoritative policy gate.

## Candidate assessment

| Candidate | Reuse opportunity | Risks / gaps | Initial decision |
|---|---|---|---|
| [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) | Python/.NET multi-agent workflows, sequential/concurrent handoffs, human-in-the-loop, checkpointing, observability | Must verify provider compatibility with Yuki's local Ollama and current adapters; dependency and API surface may be substantial | **First general-orchestration spike.** Prototype outside the default runtime; compare against the existing task/workflow engine before adopting |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Stateful graph orchestration, checkpointing, resumable execution, human review | Adds LangChain-core and related dependencies; risks duplicating Yuki's SQLite ledger and workflow engine | Strong alternative; benchmark against Microsoft Agent Framework rather than adopting both |
| [OpenHands Software Agent SDK](https://github.com/OpenHands/software-agent-sdk) | Specialized coding agent, workspace abstractions, tools, event-driven execution, optional isolated Agent Server | Coding-focused, heavier stack, and requires careful workspace/sandbox integration; SDK's own tool access must be constrained | **Best candidate for an optional coding-agent adapter** after a sandbox proof of concept |
| [Open Interpreter](https://github.com/openinterpreter/openinterpreter) | Cross-platform terminal agent, model/provider switching, sandbox and permission concepts, MCP/skills | Current upstream is a Rust-based coding agent, not a drop-in Python library; subprocess packaging and permissions need evaluation | Study its harness and sandbox model; only integrate as a separately supervised optional process if it materially improves Windows support |
| [Continue](https://github.com/continuedev/continue) | Model-provider patterns, editor integrations, coding UX | Upstream README says the repository is read-only / no longer actively maintained | Reference or selective Apache-2.0 port only; not a strategic runtime dependency |
| [AutoGen](https://github.com/microsoft/autogen) | Historical multi-agent patterns | Upstream is archived and explicitly not recommended for production; successor is Microsoft Agent Framework | Do not adopt for new production runtime |
| Google Jules | Useful product/UX benchmark for delegated coding tasks and asynchronous progress | Product/service is not equivalent to a permissively licensed source repository that Yuki can copy | Benchmark behavior and integrate public APIs only where available and terms permit; do not copy proprietary implementation |

This is an architecture shortlist, not a claim that any candidate has passed Yuki's integration or security tests. Re-check upstream status, exact release/tag, repository license, and dependency licenses at the time of adoption.

## What we should reuse first

- **Skills:** continue using the Agent Skills format and Yuki's pinned-source/provenance checks. Installing a skill must not silently execute its scripts.
- **Tool interoperability:** keep MCP behind Yuki's existing host allowlist, HTTPS/DNS protections, approval policy, response limits, and audit events.
- **Model access:** retain Yuki's provider abstraction and explicit provider/model selection; do not let a framework introduce silent cloud fallback.
- **Coding tasks:** test OpenHands SDK in a separate workspace with no host-wide filesystem access and no network by default. Expose only Yuki-approved tools.
- **Durable orchestration:** build a small comparison spike for Microsoft Agent Framework vs. LangGraph vs. Yuki's existing workflow engine. Use the same acceptance suite and a no-network mock provider first. Do not run two independent task ledgers in production.
- **Desktop automation:** keep OS control in Yuki-owned capability adapters with explicit per-action approvals. Never grant an imported agent blanket shell, keyboard, mouse, camera, or screen access.

## Mandatory intake checklist before copying or adding a dependency

1. Identify the exact upstream repository, immutable commit/tag, source file paths, and authorship.
2. Verify the file-level license and repository license. Check bundled assets, generated code, model weights, examples, and transitive dependencies separately.
3. Preserve required copyright/license notices; include license texts and attribution in the distribution where required. Record modifications and upstream provenance.
4. Generate/update the dependency lock and software bill of materials (SBOM); scan known vulnerabilities and license policy.
5. Review install/build scripts, network calls, telemetry, subprocesses, filesystem access, dynamic code execution, and update mechanisms.
6. Map all external tools to Yuki's permission and audit interfaces. Reject alternate approval paths and implicit credential access.
7. Add unit, security, cross-platform packaging, and offline/mock-provider tests. Include adversarial prompt-injection and tool-output tests.
8. Benchmark task success, recovery, latency, memory, package size, and provider coverage against the current implementation.
9. Keep the integration optional until it passes acceptance tests. Pin versions and document rollback/removal.
10. Record the decision, test evidence, and known limitations. Legal review is needed if license or provenance is ambiguous.

## Acceptance gates for a candidate adapter

- Runs on supported Windows and Linux CI.
- Supports local Ollama without requiring cloud credentials; cloud providers remain explicit opt-in.
- Cannot execute a tool outside Yuki's registered capability set.
- Requires approval for every consequential write/action unless a narrowly scoped, user-configured policy explicitly allows it.
- Records task start, tool proposal, approval, execution result, verification evidence, and failure in the same durable task timeline.
- Recovers interrupted work without automatically repeating non-idempotent actions.
- Has bounded prompts, outputs, retries, runtime, and resource usage.
- Passes dependency/license/security review and can be disabled without breaking the base CLI or desktop app.

## Delivery plan

1. Add this decision record and keep the core dependency footprint unchanged.
2. Prototype Microsoft Agent Framework and LangGraph against the same minimal task lifecycle; compare to the current engine.
3. Separately prototype OpenHands SDK for a sandboxed coding task.
4. Select at most one general orchestration engine and one optional coding-agent adapter based on evidence.
5. Only then propose a pinned optional dependency and integration PR. Do not merge the large integration PR solely because its mergeability flag is true.

## Upstream references

- Microsoft Agent Framework: https://github.com/microsoft/agent-framework
- LangGraph: https://github.com/langchain-ai/langgraph
- OpenHands Software Agent SDK: https://github.com/OpenHands/software-agent-sdk
- Open Interpreter: https://github.com/openinterpreter/openinterpreter
- Continue: https://github.com/continuedev/continue
- AutoGen (archived): https://github.com/microsoft/autogen
