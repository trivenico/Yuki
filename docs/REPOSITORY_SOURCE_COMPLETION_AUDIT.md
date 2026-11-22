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
| #1056 | Native-first provider-independent startup | Draft; current-main-based; deterministic adapter only, not a trained model; source-first tests added but not run |
| #1055 | Conversation runtime concurrent shutdown | Draft; current-main-based; serializes close and fails closed after cleanup error |
| #1054 | Inference lease expiry without replay | Draft; current-main-based; terminal `lease_expired` transition; owner-authorized retry remains separate |
| #1053 | Wake-word worker lifecycle race | Draft; current-main-based; targeted wake-word jobs passed; full quality gates still require completion |
| #1052 | Specialist worker lease heartbeat | Draft; current-main-based; worker lease-renewal change; keep isolated until combined worker contract is reconciled |
| #1050 | 100-agent workforce registry | 207 files; draft; 951 commits ahead / 605 behind `main`; keep deferred |
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
| #560 | Do not replay inference after lease expiry | PR #1054 proposes a terminal `lease_expired` transition and stale-token regression coverage; not merged or validated, so `main` still requires the fix before worker activation |
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

- Draft PR #1056 adds a deterministic `NativeCoreAdapter`, changes new-install defaults to native mode, and keeps external model adapters opt-in. This closes the startup-without-provider source gap but does not provide a trained language model or general free-form generation.
- Draft PR #1055 serializes concurrent conversation-runtime shutdown and keeps the lifecycle closed after a store-close failure; its tests have not been run.
- Draft PR #1054 changes expired inference jobs to terminal `failed/lease_expired` instead of silently re-queuing them; it is not merged and owner-authorized retry is still missing.

## 7. Recent commit-history themes

The latest 100 commits on `main` were reviewed. Recent work is concentrated in:

- Provider request/stream validation, redirect rejection, Gemini subprocess output bounds, stdin prompt delivery and cleanup.
- Desktop startup/shutdown, microphone/session teardown and partial-initialization cleanup.
- Provider-independent native-only direction in the legacy CLI.
- Documentation and release-readiness evidence.

The native-only provider work is not yet consistent across every entry point. The next code work must converge the installed CLI, API and desktop onto a provider-optional core rather than adding another parallel assistant runtime.

## 8. Completion plan

1. Finish the core source audit and map each user-facing feature to its canonical module and entry point.
2. Reconcile PR #1056 and make the application start without an external provider on CLI, API and desktop; deterministic native workflows remain to be expanded.
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

## 10. Account-scoped native command integration audit (2026-11-22)

The following open PRs now provide important pieces of the native desktop command path, but they are independent branches and must not be treated as one integrated feature:

| PR | Branch/base | Source responsibility | Integration gate |
|---|---|---|---|
| #1059 | `feat/native-intent-router` / `main` | Deterministic native intent parsing | Must remain deterministic and reject ambiguous/unsupported commands |
| #1060 | `feat/native-intent-dispatcher` / #1059 | Authorized intent dispatch | Capability checks must precede adapter resolution and side effects |
| #1061 | `feat/native-task-command-adapter` / #1059 | Native task command adapter | Must use owner-scoped TaskRuntime records |
| #1062 | `feat/native-memory-command-adapter` / #1059 | Native memory command adapter | Must resolve memory only for the authenticated actor and honor session consent |
| #1063 | `feat/native-command-runtime` / #1060 | Compose parser, dispatcher and adapters | Must preserve fail-closed authorization and audit behavior |
| #1064 | `feat/desktop-native-command-session` / #1063 | Authenticated desktop session and composer routing | Currently disables all task/memory native capabilities until account-scoped stores are wired |
| #1065 | `feat/account-owned-workflow-records` / `main` | Immutable workflow owner IDs, owner-scoped reads/listing, legacy quarantine | Must be integrated into the task runtime actually constructed by desktop composition |
| #1066 | `fix/account-owned-agent-plans` / `main` | Account-scoped persisted agent plans and idempotency | Must be composed with the same authenticated owner and owner-scoped TaskRuntime |
| #1073 | `security/account-scoped-desktop-memory` / `main` | Per-account encrypted memory files and keyring labels; session facade invalidation; account-bound backup metadata | Must be created for the authenticated actor on each desktop session and invalidated at logout |

### Required integration sequence

1. Preserve #1059 → #1060 → #1063 → #1064 as the native command runtime dependency chain; #1061 and #1062 are adapter dependencies of #1063.
2. Bring #1065's owner-aware workflow store/runtime into the desktop service composition before enabling native task list/read/create/approve/cancel capabilities.
3. Bring #1073's `create_desktop_memory(keyring, actor_id=...)` into the authenticated desktop session lifecycle before enabling native memory commands. Legacy profile-wide memory remains quarantined; do not infer ownership or auto-migrate it.
4. Bring #1066's account-scoped plan persistence into the same composition before enabling agent plan read/dispatch/cancel commands.
5. Only then replace #1064's deliberate deny-all task/memory capability callback with the authenticated role/capability mapping. Keep the denial in place until every store and adapter in the active composition is owner-scoped.
6. Reconcile desktop legacy specialist screens and APIs separately; #1064 explicitly does not claim those older surfaces are covered by native-command capability restrictions.

### Source-review notes

- #1065's workflow store adds an immutable `owner_id`, leaves pre-migration rows ownerless, and scopes ordinary account reads/listings to the owner. Service actors are a separate privileged path and must not be exposed to untrusted desktop input.
- #1066 scopes plan IDs and idempotency keys by owner, quarantines legacy ownerless plans, and checks ownership before plan reads, dispatch and cancellation.
- #1073 derives an opaque namespace from the profile UUID and authenticated actor ID, uses a separate encrypted file and OS-keyring account per namespace, refuses to inherit the legacy shared key, and invalidates retained memory facades on logout.
- The PRs are draft branches. Their existence and mergeability do not establish that the desktop's running composition includes them.
- No tests or CI passes are claimed by this source inventory. Exact-head CI and runtime acceptance remain in the later verification phase.

### Desktop composition gap confirmed during source inspection

A direct source comparison of #1064 against #1065 found that #1064's `ui/desktop/launch.py::_build_services` still constructs `WorkflowRunStore` and `TaskRuntime` from the profile-wide database without the owner-aware runtime integration. Its native capability callback deliberately returns false for every task and memory capability. This is correct fail-closed behavior today; enabling those commands before replacing the active store/runtime would expose profile-wide task data.

The same desktop bootstrap passes `task_permission_authorizer` to `SpecialistAgentCoordinator`. That callback currently maps only `task:*` actions, while the coordinator requests `agent:read`, `agent:plan`, `agent:dispatch`, `agent:cancel`, `agent:register` and `agent:manage`. Those operations therefore fail closed. Do not solve this by broadly mapping agent actions to role permissions: #1066's owner-scoped plan implementation must be integrated first, and registry mutation needs a separately defined administrative capability.

The correct follow-up is a deliberate composition change that:
- uses the #1065 owner-aware `WorkflowRunStore` / `TaskRuntime` contract in the actual desktop bootstrap;
- uses #1066 owner-scoped agent plans with the same authenticated actor identity;
- uses #1073 account-bound encrypted memory for each authenticated session;
- wires the native adapters to those exact service instances;
- only then replaces the temporary deny-all native capability callback with explicit capability authorization.

This inspection did not change runtime code. It identifies an integration boundary, not a passing test or a completed desktop feature.

### Account-memory call-site mismatch found in desktop session startup

The #1073 memory API changes `create_desktop_memory` to require the authenticated `actor_id` and to derive an account-specific encrypted filename/keyring account. The current #1064 desktop session-opening path still calls `create_desktop_memory(self.sessions.keyring)` without that required keyword. These branches therefore cannot be composed unchanged: after bringing #1073 into the desktop branch, opening a signed-in session will raise `TypeError` before the companion context is built.

The integration must call the factory only after obtaining and validating the current authenticated identity, passing `actor_id=identity.actor_id`. The memory facade must then be owned by that session and disabled/invalidated during logout/session replacement. Startup health checks must validate the account-specific encrypted store for the active identity rather than treating the legacy profile-wide `memory.enc.json` as the active account store. Do not silently copy or assign legacy shared memory to the first account.

This is a source-level cross-PR compatibility defect; no runtime patch or test result is claimed in this audit entry.

### Task-runtime and agent-coordinator branch compatibility review

A further comparison of #1064 against the account-isolation branches found two distinct integration requirements:

- **Task session revocation API:** #1065 changes `TaskRuntime.revoke_session_approvals` to require `actor_id`, and its desktop controller change passes both `actor_id=self.identity.actor_id` and the exact `session_id`. The #1064 controller still calls `revoke_session_tasks(session_id=...)` without the actor. These changes must land together; otherwise the logout cleanup path is incompatible with the owner-scoped runtime API. Do not weaken the owner requirement or restore a global revocation query.
- **Agent authorization:** #1066 scopes plan lookup, advance and cancellation to `actor_id`, but #1064 supplies the task-only `task_permission_authorizer` to the coordinator. That authorizer does not grant `agent:read/plan/dispatch/cancel/register/manage`, so the UI's specialist-plan operations remain denied. This is safe fail-closed behavior, not a reason to map every agent operation to a broad workflow role. Integrate an explicit agent capability policy tied to the authenticated actor and keep registry mutation behind a separate administrative policy.

The #1073 branch itself already updates its desktop startup call to pass `actor_id` to `create_desktop_memory`, and its controller changes bind backup/restore and logout invalidation to the authenticated account. The earlier memory call-site finding is therefore a **cross-branch composition dependency**: it is addressed within #1073, but #1064 must receive that corresponding startup/controller change when the branches are integrated.

These are source-contract findings from comparing current draft branch heads. No integration commit or test-pass claim is made here.

### Follow-up fix: account-scoped logout revocation in #1073

The latest #1073 controller patch still used the pre-isolation `revoke_session_tasks(session_id=...)` call. That does not satisfy #1065's new actor-scoped task revocation contract. Updated the #1073 controller to pass both `actor_id=self.identity.actor_id` and the exact session ID. This keeps logout cleanup scoped to the authenticated account and exact session. The change is a source-level compatibility fix; it still needs integration with the corresponding #1065 runtime/store API and regression tests on the composed branch.

### Integration sequencing correction: logout actor scope must move as one API unit

A follow-up compatibility check showed that changing only #1073's desktop controller to pass `actor_id` would break that branch against its current `companion/application.py` and `TaskRuntime` base APIs, which still accept only `session_id`. I therefore reverted that isolated controller-only change (commit `40f33837836c586ae1c7442e9b19b2755c949266`; it reverses the temporary `d2d9be4` edit).

The correct integration unit is the complete #1065 contract: update `TaskRuntime.revoke_session_approvals(session_id, *, actor_id)`, update `DesktopApplication.revoke_session_tasks(*, actor_id, session_id)` to forward both values, and update the desktop controller call site together. This must be integrated with #1073's account-memory controller changes in a composed branch and tested there. Do not land a partial actor argument change against the old application/runtime signature.

### Desktop specialist-agent authorization wired on #1064

Added `agent_permission_authorizer` in `core/auth.py`, bound to the live desktop session and exact actor identity. It deliberately separates capabilities:
- `agent:read` requires `workflow:read`.
- `agent:plan`, `agent:dispatch`, and `agent:cancel` require `workflow:approve`; dispatched tasks remain independently subject to `TaskRuntime` authorization.
- `agent:register` and `agent:manage` require `users:manage`, keeping shared specialist-registry mutation administrative.

Desktop startup now supplies this dedicated callback to `SpecialistAgentCoordinator` rather than the task-only callback. Added auth tests for live-session binding, forged actors, unknown actions, and separation of ordinary account permissions from registry administration. Commits on #1064: `824313c849547154baedb749bf1099291df988ab`, `473b5c1615282874069b1f99ff5c80ba9b24f166`, `fa410adb0459173636b35c8d4ea4bd89200f9928`, and test correction `047963258ce3c7cb5176a21dbfbc7d1c2546545a`.

This resolves the previously identified task-callback mismatch for agent operations. It does not by itself complete the #1066 owner-scoped plan integration; the #1066 coordinator must be composed with #1064 before enabling account-owned agent plans. CI/test outcomes are not yet available for these commits.

### Owner-scoped specialist-plan implementation transplanted into desktop integration branch

Integrated the two-file #1066 owner-isolation change into #1064's desktop-native branch without retargeting or merging the independent PR branch. The transplant is limited to `automation/agent_coordinator.py` and `tests/test_agent_coordinator.py`; #1064's dedicated session-bound agent authorization helper and startup wiring remain intact.

Source checks on the updated #1064 head confirm:
- plan creation persists the authenticated actor as immutable `owner_id`;
- idempotency keys are scoped to that owner;
- advance, cancellation, and plan reads select by both plan ID and authenticated owner;
- legacy ownerless plans remain quarantined rather than being assigned to an inferred account;
- coordinator tests from #1066 accompany the implementation.

Commits on #1064: `5e0615c3e1d98cd49b460006b1bb2a8e09581922` and `6c063b42df143a19cbf746d9c65730df9dd03505`.

This composes the agent-plan ownership boundary with the desktop agent authorization work. It does not yet compose #1065's owner-scoped workflow/task runtime or #1073's account-scoped memory factory and controller lifecycle. Those remain separate integration units. CI has not reported results for the new commits yet.

### Desktop account-owned task and memory composition completed on #1064

The desktop integration branch now includes the source and regression-test files from #1065 for owner-scoped workflow/task records, session approval revocation, and worker runtime behavior. It also includes #1073's account-scoped encrypted-memory implementation, backup/restore identity binding, and memory lifecycle tests.

The shared controller was composed rather than copied independently: it invalidates the active account's memory facade before logout cleanup, passes both authenticated `actor_id` and exact `session_id` to task revocation, and binds backup/restore operations to the signed-in actor. Desktop startup now creates memory with `identity.actor_id` and quarantines legacy profile-wide memory rather than opening it or inferring ownership.

Native command access was then narrowed and partially enabled:
- Read-only `task:list` and `task:read` use a live-session, actor-bound authorization helper and the now owner-scoped TaskRuntime.
- Mutating task commands remain denied.
- Native `memory:read` and `memory:write` remain denied until the authentication permission catalog explicitly models those capabilities and memory consent is integrated at the command boundary. Account isolation alone is not permission to expose memory.

Added auth tests verifying native task reads, invalid/forged session rejection, and denial of task mutation and memory commands.

Recent #1064 commits include `15cfb39949749b7452e8d62053bddfff8da4de93` through `cf795ed92e2e52f62e8443173dea39734f3bf050`; see the branch history for the complete sequence. This is a composed source branch, not a merge of the original draft PRs. Full integration tests and CI are still pending.

### Native account-scoped memory search permission boundary

The built-in role catalog now grants `memory:read` to authenticated account roles. The desktop native-command authorizer accepts only `task:list`, `task:read`, and `memory:read`, resolves the current live session on each authorization, and requires the session actor to match the requested owner. Memory search reads only the authenticated actor's account-bound `CompanionMemory` facade.

Native memory writes remain denied at the authorization layer. Although `CompanionMemory.remember` independently enforces session-scoped consent, the desktop command boundary does not yet expose a distinct explicit save-consent action. This avoids treating the existing context toggle as permission to persist new memories. Tests were added for allowed owner reads, forged actor denial, invalid session denial, and denied task mutation/memory write. Full CI remains outstanding.
