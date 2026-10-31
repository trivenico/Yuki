# Day-one capability strategy: pretrained models and open-source components

## Decision

Yuki will not train a foundation model from scratch before its first useful release. It will reuse pretrained models and mature open-source components, then add Yuki's own orchestration, memory, skills, privacy controls, and execution layer.

The objective is first-day usefulness, not a claim that a downloaded model is already a complete autonomous assistant. A model supplies language/reasoning capability; tools, durable workflows, permissions, integrations, and verification make tasks executable.

## Four capability layers

1. **Pretrained intelligence:** general reasoning, coding, fast/local routing, embeddings, vision, and speech models. Select each by measured task quality, latency, hardware footprint, privacy, and cost.
2. **Reusable agent capabilities:** reviewed skill packs, MCP integrations, browser/file/GitHub adapters, and selected upstream agent patterns.
3. **Yuki intelligence:** task planning, model routing, scoped memory, retrieval, delegation, user preferences, evaluation, and audit.
4. **Execution:** bounded tools, isolated workspaces, durable jobs, explicit approvals, observable progress, verification, and recovery.

## Reuse policy

Prefer stable interfaces and small, reviewable integrations over copying entire agent applications. Candidate upstreams and standards:

- Hermes Agent: memory, skill lifecycle, subagents, scheduling and provider patterns.
- OpenClaw: gateway, device/channel adapters, sessions and plugin patterns.
- OpenJarvis: local-first inference, model/engine separation, hardware-aware selection and evaluation telemetry.
- Model Context Protocol (MCP): external tool interoperability.
- Agent Skills / `SKILL.md`: portable task instructions and skill discovery.
- Ollama, llama.cpp and vLLM: local or self-hosted inference runtimes, chosen according to host hardware and deployment needs.

These are candidate sources, not a claim that every upstream capability is already integrated. Before importing code or installing a dependency, record the canonical source, pinned revision, license, provenance, dependency/security posture, required permissions, and the exact Yuki tests that validate the integration. Preserve required notices and attribution. Do not copy code whose license or provenance is unclear.

## Model portfolio

Use a provider-neutral model contract and allow multiple models to coexist:

| Workload | Preferred model profile | Routing policy |
|---|---|---|
| Intent classification and task routing | Small, low-latency model | Local by default |
| General conversation and summaries | Efficient instruction model | Local first; configured cloud option |
| Complex reasoning and planning | Strong reasoning model | Explicitly configured cloud or capable self-hosted model |
| Software engineering | Coding-specialized model | Local/self-hosted where quality is sufficient; cloud only with per-agent consent |
| Embeddings and retrieval | Dedicated embedding model | Local by default; version embeddings |
| Vision | Vision-language model | Only when the user enables image/screen input |
| Speech | ASR/TTS models | Optional client capability, explicit microphone/speaker controls |

Model names and tags are not quality guarantees. For every candidate, check its exact model card, license and commercial-use conditions, provenance, supported runtime, quantization, context window, memory/VRAM requirement, security history, and provider data policy. "Open weights" is not automatically equivalent to an OSI-approved open-source license. Do not automatically download, execute, or activate discovered artifacts.

## What we already have in this branch

- Provider adapters and multi-provider routing, including local Ollama, OpenAI-compatible endpoints, OCI API-key/IAM paths, and configured self-hosted endpoints.
- Hugging Face model metadata discovery and a human-reviewed model registry/evaluation path.
- A bounded model evaluation harness; current starter metrics are not sufficient alone to establish correctness or safety.
- Agent Skills markdown parsing and data-only skill packs.
- A searchable skill catalog and explicit skill approval/wiring primitives.
- An MCP HTTPS client with explicit server/tool allow-lists.
- A durable task runtime, specialist registry, coordinator and worker lifecycle.
- Local lexical memory and a persistent memory foundation.

These components have different integration/readiness levels. Their presence does not prove the full end-to-end user workflow is complete. In particular, the 21 starter profiles are metadata and role configurations, not 21 independent models or 21 independently tool-enabled agents.

## Critical next integration: turn model output into verified work

The current specialist worker intentionally returns bounded text artifacts; it does not run generated code, invoke arbitrary shell commands, or mutate workspaces. To deliver the requested day-one task execution, implement the following sequence:

1. **One shared runtime:** CLI and desktop submit tasks to the same application/task service; no duplicated planner or policy.
2. **Planner-to-skill bridge:** let the planner discover registered skills, produce typed proposals, validate arguments against schemas, and invoke only through the shared guarded skill runtime.
3. **Durable approvals:** bind approvals to exact task, agent, tool, arguments, workspace, and expiry; record decisions in the task event ledger. Do not treat a generic prior approval as permission for a different action.
4. **Read-only tools first:** add bounded file listing/reading, repository inspection, documentation search, and status checks.
5. **Approved writes and execution:** use isolated worktrees/containers, explicit diff previews, scoped filesystem access, command allow-lists, resource/time/network limits, cancellation, and rollback. Require approval before consequential writes or external actions.
6. **Verification:** run task-specific tests/checks, capture exact outputs and exit codes, distinguish proposed changes from applied changes, and report failures honestly.
7. **Live interfaces:** stream task events and cancellation state to CLI and desktop; retain durable state when clients disconnect.
8. **Evaluation gate:** test complete task scenarios against a versioned dataset, including prompt injection, privacy, permissions, failures, timeouts, retries, and cost/latency.

This is the bridge between "the model can answer" and "Yuki can safely complete a real task."

## Day-one acceptance criteria

A first useful release should be able to:

- Answer questions using a configured local or cloud model.
- Search and summarize user-provided/local documents with source references.
- Recall user-approved project context across sessions.
- Discover and invoke reviewed skills through a single permission boundary.
- Inspect an authorized Git repository and propose a change.
- Create a task plan, delegate bounded subtasks, and show progress.
- Execute an approved change in an isolated workspace, run tests, and present a diff.
- Recover or clearly mark interrupted work rather than silently claiming success.
- Run without cloud access when local models and local tools are configured.

The first release does not need custom foundation-model weights, unattended desktop control, unrestricted shell access, or self-modifying code.

## Learning without foundation-model training

Yuki should improve through a controlled loop:

1. Capture task traces and outcomes with secrets and private content redacted or excluded.
2. Ask for explicit feedback where appropriate.
3. Convert reusable corrections into candidate memory or skill changes.
4. Add regression cases to the task evaluation suite.
5. Run evaluations and security checks.
6. Require review before promoting a new skill, prompt, model route, or policy.

Do not silently train on private conversations or upload them to a third-party provider. Keep user data local by default and make any external processing explicit.

## Rollout

- **MVP:** connect the existing provider, task runtime, guarded skills and read-only tools into one CLI workflow.
- **Next:** add isolated workspace changes, test execution, approval previews and desktop parity.
- **Then:** add semantic retrieval/embeddings, model portfolio benchmarking, optional vision/speech and more integrations.
- **Later:** consider fine-tuning only when measured failures show that prompting, retrieval, tool design, or model selection cannot solve the problem.

## Release truth

Pretrained models and open-source components save substantial time, but integration, compatibility testing, security review, model downloads, hardware sizing, provider setup, and production operations still take work. Yuki can be useful on day one of its first usable release; it should not be described as production-ready until the end-to-end acceptance and security gates pass.
