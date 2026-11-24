# Audit 092 — Agent Registry Lifecycle, Dynamic Registration, and Plugin Isolation

Repository: trivenico/Yuki  
Base: main  
Scope: specialist-agent registry, runtime registration and enablement, plugin discovery/import, guarded skill execution.  
Outcome: architecture/security audit; no runtime patch submitted.

## Executive summary

Yuki already has a durable specialist registry with authorization-gated registration, enable/disable, sanitized listing, strict manifest validation, and cross-instance refresh. Runtime registration changes routing metadata only; it does not install or execute code. This is a useful separation between registering an agent and deploying executable code.

However, the registry is currently closer to an append-only catalog with an enable switch than a complete lifecycle manager. The inspected schema has no manifest revision, content digest, signer/provenance, update/retire operation, compatibility state, rollout state, or registry change history. Existing agent IDs are inserted with INSERT OR IGNORE at startup, so changed bootstrap definitions do not update persisted records. That avoids silent mutation but also means upgrades need an explicit migration/update process.

The separate PluginLoader imports Python modules from a trusted local package. Its own module documentation correctly warns that importing Python executes code with Yuki's privileges and is not a sandbox. A trusted-module allow-list helps constrain discovery, but the optional wildcard allows every discoverable module in the package. There is no package signature, artifact digest verification, isolated install/inspection phase, dependency provenance, or rollback lifecycle in the inspected loader.

The correct design is to keep agent registration declarative and separate from code installation. Code-bearing plugins should be signed, reviewed, versioned artifacts executed in a restricted worker boundary; registering or enabling a manifest must not itself import arbitrary code.

## Files inspected

- automation/agent_coordinator.py
- core/plugin_loader.py
- core/guarded_skill_runtime.py
- core/permissions.py
- core/config.py
- automation/task_runtime.py
- tests/test_agent_coordinator.py

The repository paths tests/test_plugin_loader.py and tests/test_guarded_skill_runtime.py were not present at those exact paths during this review. This does not establish that no related tests exist elsewhere.

## Positive controls observed

1. SpecialistAgent manifests are immutable dataclass values and validated for bounded IDs, names, capabilities, permissions, and supported action.
2. Agent registration requires the explicit agent:register authorization decision and rejects duplicate IDs.
3. Enable/disable requires a separate agent:manage authorization decision and a strict boolean.
4. Registry reads are authorized and omit permission details from the returned sanitized metadata.
5. SQLite is the source of truth; coordinator instances refresh persisted registry state before relevant operations.
6. Registration is declarative metadata only; it does not install or import plugin code.
7. Plugin discovery is disabled by default.
8. PluginLoader requires an explicit trusted_modules set unless a wildcard is configured; untrusted modules are skipped.
9. PluginLoader validates that discovered classes are local subclasses of the Skill base and that instances expose a name and callable execute method.
10. GuardedSkillRuntime centralizes approval checks and bounds returned data, while explicitly documenting that it does not sandbox skill code.
11. Skill approval is one-shot and bound to the digest of exact arguments, preventing reuse for different arguments.

## Findings

### P1 — Plugin import executes trusted code with the host process's privileges

PluginLoader calls importlib.import_module for each trusted module. Python module import executes top-level module code before class-shape validation. The loader's docstring explicitly acknowledges this. The Skill subclass and execute-method checks happen after import and therefore cannot contain malicious or compromised module initialization.

Risk: a compromised dependency or plugin can access the process environment, files, credentials, network, and any other privileges held by Yuki before the loader can inspect its class shape. Approval at skill invocation does not sandbox module import or skill execution.

Required direction:
- Treat plugins as privileged executable supply-chain artifacts, not data-only extensions.
- Verify publisher signature, immutable artifact digest, version, dependency lock, and provenance before installation.
- Perform static inspection and installation in a separate unprivileged staging environment.
- Execute plugin code in a dedicated isolated worker/VM with no control-plane secrets, narrow filesystem mounts, explicit egress policy, resource limits, and task-scoped credentials.
- Keep the control plane responsible for authorization and dispatch; never load untrusted plugin code into the control-plane process.

### P1 — Wildcard trusted-module configuration expands trust to future package contents

PluginLoader accepts "*" in trusted_modules, which authorizes every discoverable module in the target package. Since discovery enumerates package contents dynamically, adding a new module to that package also makes it eligible for import under wildcard configuration.

Risk: a package update or accidental file addition can silently expand the executable trust boundary without an explicit per-module approval.

Required direction: use exact module identifiers and immutable artifact digests. Reject wildcard trust in production configuration, or confine it to isolated development/test environments with an explicit unsafe-mode signal. Record the exact resolved module set and digest at startup.

### P1 — Registry manifests are not versioned, signed, or provenance-bound

SpecialistAgent contains agent ID, name, capabilities, action, permissions, and enabled state. The SQLite registry persists those values but no manifest revision, canonical digest, signer, source, approval record, compatibility range, or artifact association.

Risk: there is no durable evidence of which approved manifest definition was active when a plan was routed. A code artifact could be changed independently of its registry metadata.

Required direction: create immutable manifest revisions with canonical JSON digest, signer identity, source/release provenance, declared runtime version, and approval state. Bind every plan/subtask and worker grant to the exact manifest digest. Keep desired enabled state separate from immutable manifest content.

### P1 — Registry has no explicit update, retirement, or rollback lifecycle

The public methods inspected support register, set enabled, and list. There is no explicit update-manifest, retire/deprecate, delete, staged rollout, rollback, or compatibility transition. Bootstrap registration uses INSERT OR IGNORE, preserving existing persisted records when code defaults change.

Risk: operators cannot safely roll out a manifest revision, retire a compromised agent, or revert to a previously approved definition with an auditable state transition. Deleting rows ad hoc could break plan references and incident history.

Required direction: model lifecycle states such as staged, approved, enabled, disabled, deprecated, revoked, and retired. Use immutable revisions and append-only lifecycle events. Revocation must prevent new routing immediately and be revalidated by workers at execution; retirement should preserve historical references.

### P2 — Registry revision is not pinned through plans and dispatch

Plans persist selected agent IDs and required capabilities/permissions, but the inspected schema does not persist a registry revision or manifest digest. Registry refresh occurs around planning and dispatch, but a concurrent change is not bound to the plan's exact approved manifest snapshot.

Risk: dispatch and later audit cannot reliably establish the exact manifest version that was selected at planning time.

Required direction: persist registry revision and manifest digest in each planned subtask; transactionally revalidate that the agent is enabled and the pinned revision remains authorized before enqueue. Material changes should require replanning or fresh approval.

### P2 — Guarded invocation approval is not equivalent to plugin isolation or durable lifecycle approval

GuardedSkillRuntime centralizes one-shot argument-bound approvals and bounds result serialization. Its own documentation states it does not sandbox skill code. OneShotSkillApprovals is process-local, so approvals are not a durable multi-worker lifecycle or artifact-signing mechanism.

Risk: teams may incorrectly infer that a guarded skill invocation means plugin code is isolated. It only gates invocation through that runtime; it does not contain import-time code or host privileges.

Required direction: preserve the approval gate, but enforce execution isolation below it. Use durable actor/session-bound approval for high-risk external actions, and keep plugin installation, code review, enablement, and invocation as separate authorized transitions.

### P2 — No dependency/provenance inventory or rollback evidence in the inspected loader

PluginLoader records loaded, skipped, and failed module names and exception types. It does not produce an immutable inventory of source artifact digest, dependency tree, signer, version, or build provenance, and it does not manage rollback.

Risk: incident response cannot confidently identify which exact executable artifact was loaded or reconstruct a previous known-good plugin set.

Required direction: emit a signed deployment manifest/SBOM with artifact digests, dependencies, build provenance, approval record, and runtime compatibility. Preserve previous known-good revisions and support atomic rollback.

## Recommended lifecycle model

1. Discovered: metadata found; no code imported or executed.
2. Staged: immutable artifact copied into an isolated staging area and scanned.
3. Verified: signature, digest, dependencies, provenance, and policy checks pass.
4. Reviewed: human/automated approval binds to exact artifact and manifest digest.
5. Registered: declarative agent manifest revision persisted.
6. Enabled: desired state permits routing; readiness is still separately required.
7. Running: authenticated isolated worker executes with a task-scoped grant.
8. Disabled/revoked: new dispatch blocked and active grants revoked or allowed to drain under explicit policy.
9. Deprecated/retired: no new work; historical plans and audit references remain immutable.
10. Rolled back: previous verified revision restored through an audited atomic transition.

## Recommended implementation order

1. Define canonical versioned agent manifest and immutable digest.
2. Add append-only registry lifecycle events and revision pinning in plans.
3. Remove wildcard trusted-module mode from production and add configuration validation.
4. Add signed plugin artifact/provenance verification and SBOM inventory.
5. Build isolated plugin staging and execution workers.
6. Add revoke/drain/retire/rollback operations with policy revalidation.
7. Add tests for malicious import side effects, wildcard rejection, tampered artifacts, concurrent manifest update/dispatch, revocation during execution, and rollback.
8. Integrate registry lifecycle into the central AgentControlPlane facade from Audit 091.

## Validation and limitations

This audit is based on source inspection of the paths listed above. No tests were executed, no plugin was dynamically imported during this audit, and no production plugin environment or worker isolation boundary was inspected. No malicious plugin exploit was demonstrated.

No runtime patch was submitted. Removing wildcard trust without tracing every deployment configuration could break existing environments; adding manifest revisions alone would not contain Python import-time code. The next implementation should pair exact allow-list enforcement with artifact provenance and an isolated execution boundary.
