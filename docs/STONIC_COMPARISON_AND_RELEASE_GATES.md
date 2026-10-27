# Yuki vs. Stonic AI: product gap assessment

**Assessment date:** 2026-10-27  
**Product scope:** Yuki desktop + CLI only. No browser dashboard.

## Scope and source caveat

Stonic AI currently has multiple public-facing sites with different descriptions. The established product at stonicai.com describes a Windows AI character focused on voice, personality, memory, settings, everyday PC help, a world-monitoring view, and phone-based remote conversation. Its published terms explicitly position it as an experience-first product, not a professional business automation platform.

A separate stonicai.vercel.app site describes STONIC Gen 1 as a task-oriented personal AI with a plan-act-observe-verify loop, specialist agents, persistent memory, voice, web research, code work, project/file operations, and communication assistance. Its releases page says the installer is not yet available. These claims are treated as public product descriptions, not independent verification of implementation or reliability. Re-check source URLs before making competitive claims in public.

## Strategic conclusion

Yuki should combine an approachable, personalized Jarvis-style companion with stronger practical engineering and business capabilities. Do not clone Stonic branding or copy proprietary internals. Build Yuki's own visual identity, shared runtime, permission model, and user-controlled local-first architecture.

## Gap matrix

Status reflects repository evidence, not aspiration.

| Capability | Current evidence in Yuki | Gap / acceptance criterion |
| --- | --- | --- |
| Desktop companion | Tkinter shell launches guarded runtime; navigation and shortcuts exist | Polish layout, responsiveness, clear empty/loading/error states, persistent conversations, and desktop acceptance on Windows |
| CLI workflows | Main runtime exposes commands and scriptable entry point | Stable command reference, exit codes, non-interactive mode, structured output, integration tests |
| Voice | Optional STT/TTS/wake-word modules and settings exist | Real microphone → transcription → response → speech path tested end-to-end; permission and push-to-talk UX; wake-word opt-in and recovery |
| Personality and continuity | Persona and local memory foundations exist | Preference controls, explainable memory review/delete, continuity tests across sessions |
| Morning briefing | Read-only /briefing command reports workspace, provider config, skills, reminders, and voice setting | Test output; configurable sources/time; robust scheduling; no unsupported claims about provider readiness or live news |
| Task execution | Bounded model-guided loop; registered skill calls require fresh approval | Durable task states, cancellation, retries, recovery, action log, observed postconditions, result verification |
| Plan-act-observe-verify | Planner describes policy and verification steps; task loop is bounded | Implement a real loop that observes tool results, checks acceptance criteria, retries safely, and reports evidence |
| Specialist agents | Candidate architecture only; no verified complete coordinator established | Shared task context, constrained delegation, budgets, tool allowlists, cancellation, aggregation and evaluation |
| Project/developer workflows | Knowledge indexing, guarded skills and task foundations exist | Workspace selection/containment, repo inspection, patch/diff review, test runner, rollback, Git workflow and real project acceptance tests |
| AI/ML engineering | General provider/model foundations | Reproducible environment setup, experiment/project templates, dataset safety, evaluation harnesses, GPU/runtime diagnostics |
| Business/earning companion | Reminder/briefing foundations | Research with citations, opportunity scoring, proposal/content workflows, CRM/task tracking, budgets and explicit approval for outreach/spend; never promise income |
| Providers/settings | Settings store supports Ollama/OpenAI/Gemini/Hybrid labels; runtime support must be verified per adapter | Capability-aware UI, secret-store integration, explicit cloud consent, live connection/model checks, no silent fallback, adapters tested per provider |
| Skills/plugins | Guarded declarative skill import and MCP plugin foundations | Provenance/license, trust review, tool-specific approvals, network boundary tests, install/update/rollback and compatibility coverage |
| World monitor/research | No verified integrated live dashboard/feed established | Optional cited news/research digest with source timestamps and graceful offline mode; no false live status |
| Phone/remote companion | No supported remote companion in scope | Not planned for current desktop+CLI product; reconsider only if user changes scope |
| Safe self-improvement | Evolution/self-healing modules exist, but autonomous project builder is not production-ready | Propose-only patches by default, sandboxed tests, diff approval, branch isolation, rollback and audit evidence |
| Packaging and install | Hosted Windows packaging workflow exists on the PR | Clean Windows install/upgrade/uninstall, user-data preservation, offline/provider setup paths, signed releases/checksums where possible |
| Security/reliability | Approval and safety foundations exist | Independent review, threat model, dependency audit, permission/secret handling, recovery and failure-injection tests |

## Delivery gates

### Gate A — usable alpha

- Main branch is reconciled and required CI passes on the exact release commit.
- Clean Windows installer starts and can be uninstalled cleanly.
- CLI starts from a clean checkout/install and help/diagnostics work.
- User can configure Ollama or a supported cloud provider explicitly.
- A real provider smoke test completes successfully; no mock provider is accepted as proof.
- Chat, settings, skills, approvals, reminders, and briefing pass integration tests.
- README clearly lists known limitations.

### Gate B — dependable daily companion

- Voice path works on real Windows hardware with explicit microphone controls.
- Durable tasks expose queued/running/approval/completed/failed/cancelled states.
- Task completion is verified from observed outcomes, not model claims.
- Memory controls and reminder scheduling survive restart and handle failures.
- Desktop/CLI parity is tested for shared runtime operations.

### Gate C — professional project and business assistant

- Workspace-scoped developer tools with reviewed diffs, tests, and rollback.
- Specialist delegation and task budgets pass adversarial and regression tests.
- Research and business workflows provide sources, assumptions, and uncertainty.
- Security review and real-project acceptance tests pass.

## Honest availability estimate

A calendar date is not defensible from repository state alone. As of this assessment, the repository README explicitly says Yuki is not production-ready, and the current PR remains a draft. Passing hosted CI is necessary but does not prove real Ollama inference, voice hardware, or interactive Windows UX.

The earliest responsible release is an experimental alpha after Gate A passes. A daily-use companion requires Gate B. Professional autonomous development/business use requires Gate C. Schedule these by exit criteria rather than promising a date before Windows acceptance testing is available.

Because the maintainer currently has no PC/laptop for local acceptance, prioritize hosted tests and Windows package smoke tests, but label hardware-dependent acceptance as outstanding until a Windows tester or machine is available.