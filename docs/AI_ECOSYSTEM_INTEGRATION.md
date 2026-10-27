# Yuki AI ecosystem integration architecture

## Goal

Yuki is a CLI-first and desktop-only AI platform. Do not build a browser UI, browser dashboard, or browser-facing Yuki product. The desktop app and CLI share one runtime for providers, memory, skills, tools, approvals, task state, and audit events; neither interface may implement its own privileged execution path.

## What can and cannot be combined

### Model weights
Model weights can only be loaded by compatible inference runtimes and model architectures. We cannot merge Claude or Codex proprietary model weights into Yuki, nor combine arbitrary GLM, Kimi, Ollama, and other model checkpoints into one model simply by copying files. Open-weight models may be downloaded or hosted only after checking the exact checkpoint's license, model card, redistribution terms, hardware needs, and quantization/runtime support. Keep model artifacts outside Git and never bundle large weights into the source repository.

### Provider adapters
Expose each genuinely supported model service through a provider adapter implementing one shared contract: capabilities, availability, generate/stream, bounded timeouts, error normalization, cancellation where supported, and privacy metadata. Provider selection is explicit. No silent cloud fallback. API keys stay in the OS secret store or process environment and never in YAML, logs, or source control.

### Agent frameworks and tools
Study external agent projects for architecture and documented APIs. Prefer official packages, subprocess/CLI integration, MCP, or a narrow adapter over copying their internal core files. Keep third-party code in an isolated integration package with a pinned version, license/NOTICE record, provenance, security review, and tests. Do not import a framework's full tool registry without per-tool permission and sandbox review.

### Skills
Yuki now has an initial importer for Agent Skills-style `SKILL.md` documents. It imports only bounded YAML frontmatter and Markdown instructions as a declarative prompt skill; sibling scripts and executable tools are deliberately not imported. Skill instructions are untrusted task guidance, not permission grants. A future compatibility layer should normalize supported skill metadata and required tool declarations while requiring Yuki's own approval policy for every action.

### Desktop and CLI interfaces
Keep the native desktop UI and CLI as the only supported user interfaces. Both should call the same application services and durable task APIs. The desktop app should provide a polished familiar workspace, while the CLI remains scriptable and automation-friendly. Do not add a browser server, browser dashboard, or browser client as a product surface. If a future internal API is ever required for IPC, keep it private to the local machine and inaccessible to browser clients by default.

## Candidate ecosystem worklist

All candidates below require a current, source-level review before adoption. Listing a project here does not mean Yuki already contains it or endorses its code/license.

| Candidate | Possible contribution | Preferred integration | Gate before adoption |
| --- | --- | --- | --- |
| Hermes Agent / Nous Research | Agent workflow patterns, tool use, skills | Study public APIs and import compatible declarative skills | Verify exact repository, license, dependencies, security model, and version |
| OpenClaw | Personal-agent integrations and orchestration patterns | Connector/MCP adapter or isolated package | Audit permissions, network access, credentials, and provenance |
| OpenJarvis | Local assistant architecture and evaluation patterns | Reuse documented APIs or benchmark ideas | Confirm the exact official repository and license |
| StonicAI | Unclear until exact source repository is identified | Research only; no code import yet | User/repository identity and license verification required |
| Claude | Hosted model capability via official supported APIs | Provider adapter, if API access and terms allow | Do not copy private Claude Desktop internals or weights |
| Codex | Coding-agent capability via supported product/API/CLI surfaces | Explicit integration boundary or user-invoked CLI adapter | Do not copy proprietary internals; isolate filesystem and command permissions |
| GLM | Open/hosted model options | Adapter for supported API or compatible model runtime | Check exact GLM checkpoint/service terms and endpoint support |
| Kimi | Open/hosted model options | Adapter for supported API or compatible model runtime | Check exact Kimi checkpoint/service terms and endpoint support |
| Additional providers | Broader choice from the dashboard | Provider catalog plus adapter contract | Must pass capability, privacy, authentication, and regression tests |

## Delivery order

1. Keep the existing runtime authoritative and document interfaces.
2. Add the safe Agent Skills import path and regression tests.
3. Define provider capability and privacy metadata before adding providers.
4. Complete the desktop workspace and wire it to shared task, memory, provider, and approval services.
5. Keep the CLI scriptable and make its commands consistent with desktop actions.
6. Integrate external frameworks one at a time with pinned dependencies and a license/security review.
7. Add model downloads/hosting only through an explicit model manager with disk-space checks, checksums where available, user consent, and model-specific license notices.

## Completion criteria

An integration is not "done" because its files exist. It must have documented provenance/license, a supported adapter, security boundaries, tests, a readiness signal, clear UI setup guidance, and a failure path. README readiness claims must reflect what CI and real acceptance tests have actually verified.
