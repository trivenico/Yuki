# Open-source acceleration and reuse plan

## Goal

Accelerate Yuki by selectively adapting mature, permissively licensed components while preserving Yuki's existing tests, CI, permissions, provider governance, durable workflow state, audit trails, and local-first defaults.

An "80% faster foundation" is an engineering target, not a measured completion claim. Reuse does not transfer upstream production readiness to Yuki. Every adapted component must pass Yuki's own integration, security, platform, and regression gates.

## Pinned source review set

These exact upstream revisions were inspected on 2026-11-05. Re-check the license, notices, security advisories, and upstream diff before importing any additional files.

| Upstream | Revision | Declared license | Candidate reuse |
|---|---|---|---|
| [eVi](https://github.com/evi-assistant/evi-ai) | `a07852f74cd1a27709588a833a8981ddf5946e14` | MIT | Shared-core CLI/web/desktop architecture; Tauri 2 desktop shell patterns |
| [Personal Jarvis](https://github.com/tbckdev/PersonalJarvis) | `bfc177ad3c8f3a79b4433671cf5f8c0ec12e7406` | Apache-2.0 | Desktop agent workspace, persistent specialist agents, voice/companion interaction patterns |
| [OpenWOP](https://github.com/openwop/openwop) | `ec214ef38d1f515c275abc48194e4ec73ed9d3be` | Apache-2.0 code; CC BY 4.0 specification/docs | Workflow event contracts, approval interrupts, replay and conformance ideas |
| [Thoth](https://github.com/tokwalabs/Thoth) | `542bc106224df870fb5c1a2db8e0666b711c8a76` | Apache-2.0 | SQLite-backed knowledge graph and memory lifecycle ideas |

## Reuse decisions

### 1. eVi — first desktop and shared-interface reference

Inspected its MIT license, Python package metadata, desktop README, Tauri dependency manifest, Rust shell entry point, Tauri build script, capability manifest, and desktop configuration.

Adapt the Tauri 2 packaging and process-lifecycle pattern into a Yuki-owned desktop application. Do not copy its eVi-specific sidecar, token format, updater keys, app identifiers, endpoints, or configuration unchanged. Yuki's desktop must launch the Yuki ASGI runtime, bind to loopback by default, use a per-install secret, validate origin/host, and terminate its child process on shutdown.

Yuki already has `api/runtime.py`, `api/web.py`, `api/conversations.py`, and `ui/web/`. However, the ASGI runtime still needs a supported production composition/bootstrap entry point. Do not ship a desktop shell that points at an unlaunchable or unauthenticated server.

### 2. Personal Jarvis — agent workspace and companion reference

Use as a design/code reference for persistent specialist identities, multi-agent workspaces, voice interaction, and desktop supervision. Before copying implementation files, inspect `AGENTS.md`, the exact source module, its direct imports, bundled assets, nested licenses, and platform assumptions. Do not import its shell execution, computer-use, plugin, or self-modification permissions as defaults. Yuki's authorization and audit services remain authoritative.

### 3. OpenWOP — workflow interoperability reference

Prefer adopting stable, relevant schema/contracts and conformance cases over replacing Yuki's workflow engine. Preserve Yuki's durable store and governed provider execution path. Do not add a second competing workflow runtime. Attribute any copied specification text under CC BY 4.0 and preserve Apache-2.0 notices for copied code.

### 4. Thoth — memory and knowledge graph reference

Inspect schema, migration, data ownership, graph consistency, vector index lifecycle, and dependency footprint before porting. The reviewed graph module relies on SQLite, NetworkX, NumPy, and FAISS and uses process-global data-directory configuration. Do not copy it wholesale into Yuki's current owner-scoped memory stores. First define an explicit Yuki memory schema and migration/rollback strategy.

## Required import procedure

For every upstream file or directory copied/adapted:

1. Pin the exact repository commit and source path.
2. Verify the license applies to that file; inspect nested package licenses and generated/vendor assets separately.
3. Preserve copyright, license, NOTICE, patent, and attribution requirements. Add a Yuki mapping from source path/revision to destination path.
4. Record all modifications and Yuki-specific adapters.
5. Review dependency licenses, versions, security advisories, transitive dependencies, and platform support.
6. Search for embedded endpoints, credentials, telemetry, update keys, unsafe defaults, shell execution, and network listeners.
7. Add focused unit tests, integration tests, security tests, and platform-gated tests. Keep real-provider tests explicitly opt-in and do not substitute mock providers for real conformance.
8. Run existing CI and new gates against the exact commit. Do not remove or weaken existing tests to make an import pass.
9. Document unsupported upstream features and any behavior intentionally not imported.

## Architecture target

One Yuki-owned application core must power:

- Yuki AI conversation and provider runtime;
- Yuki Agent planner, coordinator, specialist agents, and durable workflows;
- Yuki Companion identity, user-approved memory, reminders, and proactive suggestions;
- Yuki CLI;
- Yuki Desktop (Windows first, then macOS/Linux);
- Web UI and future remote/device clients.

All interfaces call the same application services. UI packages must not implement separate model routing, permissions, memory, task execution, or audit logic. Desktop observation and computer interaction remain opt-in, visible, device-bound, and separately permissioned.

## Current baseline and limitations

At the reviewed Yuki head `d9abd90e1bad8676cdcd81cda54d1e7e81987927`:

- `yuki_cli.py` provides basic doctor, provider-status, ask, and chat commands.
- `yuki.py` is a separate legacy terminal runtime; CLI consolidation remains required.
- `companion/` contains persona, memory, identity labels, reminders, and related foundations, but not one fully integrated persistent companion loop.
- `ui/web/` and the authenticated conversation API exist.
- `api/runtime.py` composes an ASGI runtime from injected auth and agent-loop services, but no production application bootstrap/launcher was found in the API package.
- No native desktop application exists yet.
- The exact-SHA CI state at review time had workflow-engine safety and durable-workflow-store runs successful; quality gates and dependency audit were pending.

No upstream implementation files have been copied by this inventory change. The first code port must be a small, isolated slice with provenance and tests, not a bulk directory transplant.

## Delivery order

1. Complete the API application bootstrap and one shared service composition boundary.
2. Consolidate CLI commands onto that boundary without removing guarded legacy workflows.
3. Port the minimal Tauri 2 shell and adapt its launcher to the real Yuki ASGI server.
4. Connect companion memory/reminders/persona to the same conversation and task services.
5. Evaluate OpenWOP interoperability against Yuki durable workflows.
6. Evaluate a knowledge-graph extension only after memory migrations and tenant/owner isolation are designed.
7. Add Windows/Linux/macOS CI coverage and real-device acceptance tests when hardware is available.
