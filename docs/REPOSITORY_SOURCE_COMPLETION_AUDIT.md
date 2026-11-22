# Repository source-completion audit

**Snapshot:** 20 November 2026  
**Repository:** `trivenico/Yuki`  
**Default branch inspected:** `main`  
**Purpose:** source and integration inventory before the next broad CI/runtime campaign.

This is a repository inventory, not a release certification. Counts are from the GitHub tree, open PR pages, and open-issue search at the snapshot time. The current source tree is large, but file count and PR count are not measures of usable product completion.

## 1. Repository inventory

| Area | Count / observation |
|---|---:|
| Tracked files on `main` | 837 |
| Python test files | 257 |
| Documentation files | 159 |
| Open pull requests | 620 |
| Open issues | 31 |
| Open PRs marked draft | 488 |
| Open PRs marked ready for review | 132 |
| Open PRs targeting `main` directly | 179 |
| Open PRs targeting another feature branch | 441 |

The open PRs are heavily stacked. A large number of branches are dependent steps in long chains, not independent merge candidates. Merging by age, title, or green individual checks would be unsafe.

Top-level tracked-file distribution includes: `core/` 97, `tests/` 255, `docs/` 158, `skills/` 55, `automation/` 34, `ui/` 31, `defense/` 25, `voice/` 20, `providers/` 16, and `companion/` 14.

## 2. Main-branch source baseline

The current source already contains substantial foundations:

- Shared conversation application runtime and owner-scoped SQLite conversation store.
- Conversation generation leases, heartbeat renewal, fencing, cancellation and crash recovery.
- Provider adapters for OpenAI-compatible APIs, Ollama, Gemini CLI and hybrid routing.
- Local CLI composition with an empty guarded-tool registry by default.
- Built-in authentication, durable workflow/task state, approval gates and audit primitives.
- Desktop authentication, shared conversation/controller composition, lifecycle cleanup and optional voice services.
- Defensive security/OSINT analysis and offline PCAP/PCAPNG parsing foundations.
- Annotation, feedback, benchmark and model-evaluation foundations.
- Device pairing, persistent inference jobs, authenticated inference API, local worker and remote worker client source files.

These are foundations, not proof that every workflow is integrated or production-ready.

## 3. Highest-priority source-completion gaps

### P0 — usable personal-assistant MVP

1. **Provider-independent startup across every entry point.** The legacy `yuki.py` path has native-only selection, but the installed CLI composition in `yuki_cli.py` / `core/local_cli_runtime.py`, API runtime and desktop composition still construct provider-backed execution paths. External providers must be optional everywhere. No mock substitution in production.
2. **Native local interaction.** Build a real provider-independent command/intent layer for help, status, local memory, task inspection, planning, approvals and other deterministic workflows. Be explicit that free-form natural-language generation is unavailable until Yuki's own model exists or an optional provider is enabled.
3. **One canonical application lifecycle.** CLI, API and desktop should use the same application services, owner identity, conversation store, task runtime, memory and shutdown semantics.
4. **First-run onboarding.** Issue #347 tracks guided onboarding, capability/readiness reporting, optional provider setup, private data location, permission review and a safe first conversation.
5. **Memory and local persistence integrity.** Issues #338/#339 track concurrent JSON read-modify-write risks in task, memory, learning and audit stores. Prefer transactional storage where concurrency is expected; preserve migrations, backups and privacy.
6. **Authentication correctness and local DB protections.** Issue #553 identifies a login lockout-expiry bug; a source fix and regression test have now been pushed, pending verification. Issue #269 tracks restrictive auth database/WAL/SHM permissions.
7. **Desktop product completion.** Shared runtime and lifecycle foundations exist, but onboarding, integrated memory controls, task/activity views, diagnostics, and supported-platform packaging still need a coherent end-to-end path.

### P1 — safe execution and extensibility

- Durable task idempotency, claim fencing, lease renewal, cancellation, recovery and actor-attributed audit.
- Approval-bound tools with one consistent policy boundary across CLI, API and desktop.
- Sandboxed execution with real trusted isolation evidence before enabling arbitrary code or self-upgrade.
- Skill install/list/inspect/update/remove with pinned provenance and untrusted-content handling (issue #368).
- Backup/restore, retention, migration and failure semantics.

### P2 — defer until the personal MVP is usable

- Distributed inference/device fleet and multi-device scheduling.
- 100-role workforce, parallel fine-tuning/SFT, voice cloning, wake-word model training.
- Broad OAuth/MCP connector expansion, income operations, annotation platform expansion.
- Live host firewall changes, autonomous remediation and autonomous self-upgrade promotion.

These may be valuable later, but they must not block the first dependable local assistant.

## 4. PR and branch audit

### Mainline-relevant open PRs

| PR | Scope | Current integration concern |
|---|---|---|
| #1050 | 100-agent workforce registry | 207 files; draft; 951 commits ahead / 601 behind `main`; keep deferred |
| #1049 | SFT/few-shot learning foundations | Draft; learning pipeline is not MVP-critical |
| #1048 | system-prompt-aware model evaluation | Draft; useful later for model quality |
| #1047 | prompt-first fine-tuning gates | Docs; no model training completed |
| #1046 | specialist role manifests | Draft; do not activate role slots without ready handlers |
| #1045 | 100-role company charter | Docs; role definitions are not deployed agents |
| #1044/#1042 | consent-gated voice profile lifecycle | Optional voice extension; not required for first usable MVP |
| #1040 | desktop security dashboard integration | Open and ready; useful optional defensive UI, not core onboarding |
| #1033 | dry-run-only firewall hardening | Draft; remains plan-only |
| #1029/#1027/#1011 | wake-word controls/training/voice state | Stacked or model-training-dependent; physical-device acceptance remains |
| #1000 | desktop canonical conversation runtime | 82 files / 8,411 additions; 320 commits; targets `feat/desktop-architecture-foundation`, not `main` |
| #999 | desktop shared conversation adapter | 18 files / 2,289 additions; 63 commits; targets `feat/owner-agent-execution-component`, not `main` |
| #946 | owner-scoped persistent conversation memory | 16 files / 1,322 additions; 52 commits; targets `test/concurrent-approved-action-claim`, not `main` |
| #998–#990 | memory backup, provenance, retention and storage | Multi-PR dependency chain; review as one coherent memory lifecycle |
| #538 | remote inference worker transport | 55 commits, 29 files, about 2,500 additions; based on `feature/distributed-model-placement`, explicitly not production-ready |
| #574 | atomic device resource reservations | Draft; dependent on inference scheduling and claim fencing |

The 620-open-PR inventory includes 441 stacked PRs. Do not attempt a mass merge or automatic rebase. The next integration pass should identify one canonical branch per MVP workstream, close or supersede duplicate attempts only after preserving useful changes, and keep feature PRs small.

### Inference chain discrepancy

- PR #534 is marked merged with merge commit `a265e7f4687e3fd9e563875da25697ae47c252e0`, whose diff contains `core/distributed_model_scheduler.py`.
- That scheduler file is absent from the current `main` tree.
- PR #535's `core/device_pairing.py` and `core/inference_jobs.py` are present on `main`.
- PR #536 (local worker) is closed but unmerged; the local worker file is nevertheless present on `main`.
- PR #537 is closed but unmerged; authenticated inference routes are present through `api/inference.py` and `api/runtime.py`.
- PR #538 remains open and stacked on `feature/distributed-model-placement`.

This is a history/integration discrepancy, not a reason to blindly merge the old scheduler. The scheduler is an optional multi-device feature and remains deferred until its resource model, reservation contract and execution safety are complete.

## 5. Open issue triage

| Issue | Topic | Source-completion status |
|---|---|---|
| #553 | Login lockout re-triggers after expiry | Fix and regression test pushed; pending verification |
| #559 | Missing inference modules in CI | The named pairing/local/remote client files now exist on `main`; keep open until exact-head CI and the remaining inference chain are reconciled |
| #560 | Do not replay inference after lease expiry | Open; current `InferenceJobService.claim_next` still returns expired running jobs to `queued`; block worker activation |
| #561 | Per-claim inference fencing | Current job code has hashed lease tokens, but review the complete renewal/reclaim contract before dispatch |
| #562 | Inference wall-clock deadline and lease renewal | Open; local worker has no heartbeat and socket timeout is not a total deadline |
| #558 | Specialist worker lease renewal | Open; long-running handler lease renewal must be coordinated with worker completion fencing |
| #567/#570/#573 | Effective cgroup resources and fractional CPU | Open; scheduler/resource discovery must not overstate usable capacity |
| #568/#574/#575/#576 | Atomic resource reservations and job/reservation fencing | Open; no inference dispatch before reservation ownership is atomic and recoverable |
| #565/#566/#563/#564 | Device revocation, enrollment and heartbeat safety | Open; complete owner-authenticated pairing and immediate revocation semantics |
| #347 | Desktop app and guided onboarding | P0 product work |
| #338/#339 | Local persistence concurrency/failure semantics | Cross-process JSON locking/atomic writes now cover core local memory, reminders, evolution, task, learning and metadata-audit stores; remaining feature stores and exact-head CI still pending |
| #269 | Auth database permissions | P0 security hardening |
| #368 | Agent Skills repository install/update lifecycle | P1 extensibility |
| #367 | Windows/Linux desktop packaging and release smoke tests | P0/P1 delivery gate; issue explicitly excludes macOS |
| #327/#331/#317 | Approval review and rootless isolation acceptance | Release/security gates; self-upgrade remains disabled |
| #495 | Shared provider control plane | Multi-worker deployment feature; defer for single-user MVP |
| #496 | Phase ledger governance | Documentation/governance cleanup; do not let phase serials drive product priority |
| #55 | Upstream reuse/license audit | Do before copying/vendoring upstream code |
| #499 | Hugging Face model metadata update | Informational; optional model scouting |
| #9 | Historical Phase 9 CI issue | Re-evaluate against current source; likely stale historical issue |

## 6. Recent source-completion work

Since the initial inventory, the following source-only changes have been pushed to `main`:

- `core/json_file_store.py` adds process/thread locking and durable atomic JSON replacement.
- Companion memory, reminders, evolution state, legacy tasks, learning history and the legacy metadata audit log now serialize read-modify-write transactions.
- Stale task-list snapshots are rejected instead of overwriting concurrent updates.
- Regression tests cover independent store instances, stale task snapshots, lock-file symlinks, local data ignore rules, and concurrent learning/audit writes. These tests have not yet been run.
- Authentication lockout expiry fix and regression test are present; verification remains pending.
- Private SQLite path protections and `.gitignore` coverage are documented; cross-platform and backup acceptance remain pending.

## 7. Recent commit-history themes

The latest 100 commits on `main` were reviewed. Recent work is concentrated in:

- Provider request/stream validation, redirect rejection, Gemini subprocess output bounds, stdin prompt delivery and cleanup.
- Desktop startup/shutdown, microphone/session teardown and partial-initialization cleanup.
- Provider-independent native-only direction in the legacy CLI.
- Documentation and release-readiness evidence.

The native-only provider work is not yet consistent across every entry point. The next code work must converge the installed CLI, API and desktop onto a provider-optional core rather than adding another parallel assistant runtime.

## 8. Completion plan

1. Finish the core source audit and map each user-facing feature to its canonical module and entry point.
2. Make the application start without an external provider on CLI, API and desktop.
3. Implement deterministic native commands and local task/memory workflows.
4. Consolidate memory, task, audit and backup persistence around explicit transactional/failure contracts.
5. Complete first-run onboarding and a single polished desktop/CLI first-conversation path.
6. Reconcile only the PRs required for those MVP workstreams; defer experimental and stacked branches.
7. Once source completion is stable, run the focused test suite, full cross-platform CI, runtime acceptance and packaging/release checks.

## 9. Verification discipline

- No local test run is claimed from GitHub connector inspection.
- No CI pass is inferred from a PR being closed or marked merged.
- A source file existing does not prove its feature is wired or safe to execute.
- Model/provider status must distinguish configured, installed, reachable and inference-verified.
- External AI models remain optional adapters. Yuki owns its identity, data, policies, planning, task state and native workflows.
- The repository remains not production-ready until the release gates in `docs/RELEASE_READINESS.md` are met.


## Desktop native-command integration follow-up (2026-11-22)

The desktop integration branch now contains the account-owned workflow implementation and its regression coverage from the task-runtime ownership work:

- `WorkflowRunStore` assigns immutable `owner_id` values to new account tasks, quarantines legacy rows with NULL owners, and exposes owner-filtered lookup/list APIs.
- `TaskRuntime` applies owner checks to reads, event access, approvals, mutations, retries, cancellation, and exact-session approval revocation. Service actors are explicitly configured rather than inferred.
- The desktop application and logout controller pass both the authenticated actor ID and exact session ID to revocation.
- Specialist plan persistence is owner-scoped, including idempotency, reads, dispatch and cancellation.
- Desktop memory is created with the authenticated actor ID and uses the account-bound encrypted-memory backend; retained facades are invalidated during logout.
- Native task list/read are now enabled at the command boundary because the runtime enforces immutable owner scope. Native memory search remains account-bound and requires `memory:read`.
- Native `memory:remember` remains denied at the capability boundary. The explicit memory-saving consent lifecycle still needs a user-facing enable/disable action; do not grant `memory:write` merely to make the command work.

A source review caught and removed an obsolete fail-closed task-read override after confirming the owner-scoped store/runtime and owner-isolation regression tests were present on this branch. This is a capability enablement backed by the actual owner checks, not a relaxation of the underlying store policy.

Remaining work before declaring the desktop slice complete:
1. Add a deliberate, user-visible memory-saving consent control and bind its lifecycle to the authenticated desktop session.
2. Audit every desktop memory read/write/backup/restore path for account binding and explicit confirmation.
3. Review the full native command surface and all task-runtime call sites for owner and session enforcement.
4. Run the complete regression suite and CI only after the source integration pass is complete.

No claim is made here that the latest integration head has passed CI or runtime tests.


## Explicit desktop memory-saving consent (2026-11-22)

The desktop UI now exposes two independent, session-only controls:
- **Allow saving memories** calls the authenticated controller's explicit enable/disable methods. It defaults off for each new desktop memory facade and is reset/disabled during session teardown.
- **Use saved memory in replies** remains a separate context-use control; enabling context does not grant write consent.

The controller validates the exact live desktop identity before changing or reading the saving-consent state. Disabling saving stops future writes and disables context use without deleting existing memory entries. Logout invalidates the account-bound memory facade before slower cleanup and clears both UI toggles.

Native `memory:write` now requires all of:
1. A currently valid authenticated session.
2. The actor ID matching the session's account.
3. The account role containing `memory:write`.
4. The session's explicit memory-saving toggle being enabled.

The memory adapter still calls `CompanionMemory.remember`, which independently enforces its consent state. Exceptions from the session/consent provider fail closed. The role catalog now exposes the `memory:write` permission to built-in roles, but it does not activate saving: the explicit per-session control remains mandatory.

Regression tests were added for consent-off denial, consent-on allowance, consent withdrawal, forged actor denial, and invalid-session denial. These changes are source-complete at the integration layer but have not yet been verified by a completed CI run.


## 6. Desktop integration follow-up — 22 November 2026

A source pass on `feat/desktop-native-command-session` found two additional integration conditions that were not captured by the earlier inventory:

- Chat and memory-consent operations must participate in the same in-flight operation gate as specialist and native-command calls. Otherwise logout can begin invalidating the authenticated runtime while a chat request is still active. The bridge now gates all chat entry points and regression coverage exercises both chat and native-command/logout races.
- The desktop memory-saving checkbox must reach the authenticated conversation facade, not merely exist in the UI and native-command authorizer. The facade now exposes live-session-checked `set_memory_saving_enabled` / `memory_saving_enabled` methods, the chat adapter and bridge route them through their operation gates, and invalidation disables both write and context consent.

Regression coverage was added in `tests/test_authenticated_desktop_memory_consent.py`. These are source-level changes awaiting the PR's automated checks; this audit does not claim test execution or release readiness.


### 7. Desktop memory facade revocation follow-up — 22 November 2026

- A further logout-path audit found that the authenticated conversation invalidated its own session gate and disabled memory writes/context, but did not invoke the memory facade's permanent `invalidate()` operation.
- This left a retained reference to the facade able to call read/delete/list operations after logout, despite the desktop session itself being closed.
- Logout now prefers permanent memory-facade invalidation when the configured facade supports it. The fallback remains fail-closed for older facades by disabling saving and context consent.
- Added a regression assertion that a retained facade rejects memory access after authenticated conversation invalidation.
- This is a source-level correction only. CI/test execution has not been confirmed; the PR remains draft pending the broader source-completion and verification phases.


### 8. Retryable desktop session invalidation — 22 November 2026

- Reviewing the new permanent memory invalidation path exposed a retryability defect: the authenticated conversation marked itself invalidated before cleanup, then returned immediately on later invalidation attempts even if the first cleanup had failed.
- The authorization gate still closes immediately, but subsequent logout attempts now re-run idempotent cleanup and wait for any active sends. This lets the owning desktop lifecycle retry failed memory/runtime teardown without reopening authenticated operations.
- Added regression coverage with a transient memory-invalidation failure followed by a successful retry.
- Source correction is committed; CI and full regression execution remain pending.


### 9. Desktop voice-worker ownership and deferred teardown — 22 November 2026

- The microphone capture/transcription worker uses the shared single-thread Qt pool, but microphone disable, sign-out, and window shutdown previously closed its `VoiceSession` before the worker had necessarily returned.
- Voice revocation now disables the session immediately (setting its cancellation event and disabling the STT backend) while deferring `close()` whenever the active capture worker still owns that session.
- The worker completion signal carries its worker identity. A completion for a revoked/replaced session discards its transcript and closes the retired session only after capture/transcription has returned.
- Applied the same ownership rule to explicit microphone disable, sign-out, authenticated UI cleanup, and window shutdown. Idle sessions still close immediately; interruption errors are surfaced without skipping safe cleanup.
- Added helper-level regression tests for deferred close, immediate idle close, and cleanup after interruption failure. These tests have been authored but not executed in this environment.


### 10. Desktop speech and stale voice callback ownership — 22 November 2026

- Authentication expiry could clear the authenticated UI while a spoken-reply worker still owned the TTS backend. The UI cleanup path now revokes speech immediately and defers backend close until the matching worker completion callback; it retains the worker handle instead of dropping the cleanup owner.
- A microphone worker could also complete after its consent session had been revoked or replaced. Its callback previously updated shared busy controls and status before rejecting the transcript, which could overwrite UI state belonging to a later sign-in.
- Voice completion now requires exact session identity and enabled consent before publishing a transcript or changing active-session controls. Retired workers only close their own backend and do not reset shared busy state for a newer session.
- Added helper-level regression tests for active/idle speech retirement and exact-current-session voice completion checks. These tests are authored but have not been executed here; CI status must be checked on the latest PR head.
