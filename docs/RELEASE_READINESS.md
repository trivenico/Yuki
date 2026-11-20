# Yuki release readiness and progress

**Snapshot:** 20 November 2026  
**Overall estimate:** approximately **40% of the intended personal-assistant MVP**. This is an engineering estimate, not a test-derived metric. It weights user-facing capability, integration, reliability, security, and packaging—not the number of modules or commits.

## What the percentage means

| Workstream | Estimated completion | Evidence / remaining gap |
|---|---:|---|
| Core CLI, local memory, planning, guarded skills | 65% | Useful prototype workflows exist; end-to-end acceptance and UX consistency remain |
| Model/provider experience | 20% | Provider abstractions and evaluation adapters exist; normal user-facing provider setup, robust streaming/cancellation, and real-provider acceptance remain |
| Agent/task orchestration | 45% | Durable task state, specialist worker, approvals, planning and result review exist; safe tool execution integration, recovery and full lifecycle remain |
| Security and isolated execution | 25% | Strong policy and threat-model foundations; trusted sandbox attestation/backend and independent review are not complete |
| Companion UI, desktop and voice | 30% | UI/voice components and packaging workflows exist; polished integrated experience, wake-word reliability and cross-platform acceptance remain |
| Defensive security and OSINT | 60% | Offline PCAP/PCAPNG, metadata parsing, Nuclei report import and technical-indicator graph foundations exist; integrated workspace, evidence UX and authorization controls remain |
| Annotation, feedback and evaluation | 55% | Bounded dataset adapters, review, preference feedback, benchmark registry and durable evaluation exist; semantic evaluation, real model runs and release gates remain |
| Installation, portability and operations | 35% | Starter launchers and data-path handling exist; self-contained packaging, clean-install matrix, backup/restore and upgrade/recovery remain |

These workstream estimates are rounded and overlap in places; they should not be summed or treated as precise measurements. The overall estimate is rounded to the nearest five percentage points.

## What you can use now

Yuki is a **restricted developer preview**, not a finished JARVIS-style assistant. The current repository documents a Python CLI and starter launchers. Basic local workflows—guarded skills, local notes/memory, planning proposals and selected read-only integrations—are available in the current codebase, subject to dependencies and configuration. Follow the setup instructions in [README](../README.md) and [portable SSD guide](PORTABLE_SSD.md).

Do not treat these as production-ready:
- autonomous shell/code execution or self-modifying behavior;
- active network testing outside an explicitly authorized isolated lab;
- unreviewed third-party Python plugins;
- a fully sandboxed generated-code runner;
- always-on wake-word or reliable unattended operation;
- cloud/provider integrations that have not been configured and acceptance-tested on the target machine.

## Release gates

### Gate A — usable personal MVP
- [ ] One supported provider works end-to-end with secure credential handling, explicit model selection, timeouts, cancellation, and clear errors.
- [ ] CLI and desktop use the same application/service layer for core chat, memory, tasks, approvals, and settings.
- [ ] Fresh-install instructions pass on the target operating system; core workflows pass acceptance tests.
- [ ] Durable task recovery, cancellation, idempotency and audit behavior are verified.
- [ ] Backup/restore and private data-directory behavior are tested.
- [ ] All relevant GitHub Actions checks pass on the release commit.

### Gate B — safe developer and security workspaces
- [ ] A trusted isolated execution backend is implemented and independently validated; host execution is not accepted as a sandbox.
- [ ] Patch review, test execution, deadlines, cancellation and rollback are exercised end-to-end.
- [ ] Network tools remain passive/offline by default; active tests require explicit scope, authorization and verified isolation.
- [ ] Threat model and security-sensitive workflows receive independent review.

### Gate C — polished always-available companion
- [ ] Desktop packaging and upgrades pass on supported operating systems.
- [ ] Voice capture, wake-word, interruption and privacy controls pass hardware acceptance tests.
- [ ] Observability, crash recovery, resource limits, retention and operational diagnostics are complete.

A Gate A release can be useful without claiming Gate B or C is complete. Full JARVIS-like autonomy is not a release criterion and must not bypass owner approval or security controls.

## Timing

No calendar date is committed. A realistic usable-MVP date depends on the target operating system, chosen model/provider, hardware, and whether CI reveals regressions. The next milestone is Gate A, not all 100 planned specialist roles. After Gate A passes, Yuki can be released as a practical personal MVP while the deeper sandbox, security workspace and voice work continue.

## Verification discipline

Commits and files are implementation evidence, not proof of runtime readiness. Do not mark a gate complete until its tests and acceptance checks pass on the release commit. On 20 November 2026, commit `e3fb7e3085dfe86893d2b8adb484707bd966a9af` passed the full 13-job quality workflow on its second attempt: Python 3.11 unit/quality checks on Windows, Linux and macOS; installed CLI checks on Python 3.10/3.11/3.12 across all three operating systems; and the Ubuntu Qt offscreen smoke test. The first Windows Python 3.11 attempt reported a SQLite `database disk image is malformed` error in the evaluation timeout test; the rerun passed without a code change. The cause is therefore unconfirmed and remains a watch item rather than a resolved defect. The native desktop packaging workflow also completed unsigned bundle builds for Windows, macOS and Linux and its release-build provenance attestation job. These are unsigned developer bundles, not production installers. Local tests were not executed through the GitHub connector workflow.
