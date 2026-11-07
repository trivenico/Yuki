# Phase 235 — Yuki Reverse Engineering Division

**Status:** Evidence contract foundation implemented; acquisition and analysis adapters are not yet implemented.

## Objective

Give Yuki a lawful, evidence-first ability to investigate software and digital artifacts that the user owns or is explicitly authorized to examine. Yuki should be able to explain observed behavior, identify uncertainty, and use verified findings to implement and test an independently designed solution.

This is a capability family inside Yuki's AI Engineering Director—not a claim of parity with REA and not a promise to recover original source code.

## REA review and lessons

The reviewed `morluto/rea` project demonstrates a strong integration pattern:

1. One investigation workflow exposed through MCP and CLI.
2. Specialized analysis providers for different artifact types.
3. Persistent, content-addressed Evidence records.
4. Explicit coverage, limitations, provenance and integrity validation.
5. Follow-up inspection and comparison over retained evidence.
6. A clear distinction between static inspection and runtime capture.

REA's MIT license applies to REA-authored code, not automatically to its third-party tools, data, dependencies or commercial analysis providers. Yuki will not copy implementation code or bundle external tools without a separate license and security review.

## Yuki capability architecture

### 1. Investigation Director

Accept a target, the user's authorization scope, the question to answer, and a bounded investigation budget. Select only compatible analysis capabilities. Ask for clarification or approval before any runtime interaction or sensitive acquisition.

### 2. Evidence and provenance core

The initial `reverse_engineering.evidence` module provides:
- Versioned, strict evidence envelopes.
- Content-addressed SHA-256 evidence identity.
- Canonical JSON serialization.
- Bounded payload size, nesting, item count and string length.
- Explicit target and provider identity.
- Coverage state (`complete`, `partial`, `unknown`).
- Limitations and adapter provenance.
- Integrity verification before summaries are produced.

The contract validates already-collected observations. It does not access files, run commands, connect to devices, or prove that a provider's observations are truthful.

### 3. Planned analysis adapters

| Domain | First capabilities | Boundary |
|---|---|---|
| Source and packages | file inventory, hashes, manifests, dependency graph | Read-only; no implicit package install |
| Web applications | DOM/accessibility snapshot, routes, script inventory, authorized browser traces | Explicit origin allowlist and browser permission |
| JavaScript/Electron | module/import graph, IPC and preload boundary inventory, source maps | Static first; no app execution |
| Native binaries | headers, sections, symbols, strings, imports, call graph and decompiler handoff | Tool/provider-specific; no execution by default |
| .NET and managed code | assemblies, types, methods, references | Static inspection |
| Firmware and archives | bounded inventory, format identification, extraction manifests | Path traversal and decompression-bomb controls |
| Model artifacts | GGUF/safetensors metadata, tensor declarations, digest | Never load weights during metadata inspection |
| Network captures | pcap/HAR parsing, endpoints, protocol summaries | Offline captures only initially; no interception |
| Runtime behavior | process/file/network observations and reproducible traces | Opt-in, disposable isolated target and explicit approval |

### 4. Investigation workflow

`request -> authorize -> identify target -> choose adapter -> collect observations -> normalize Evidence -> verify integrity -> analyze gaps -> follow-up probes -> engineering brief -> implementation -> tests -> independent review`

A finding must cite evidence IDs and locations. Missing coverage must remain unknown; empty data is not proof of absence. Static analysis cannot establish runtime behavior.

### 5. Safety and authorization

- Default to static, read-only analysis.
- Require the user to confirm ownership or authorization for targets.
- Scope file access to explicit roots and reject symlinks/path traversal.
- Never execute a target merely to inspect it.
- Runtime capture requires separate approval, resource/time limits and a disposable environment.
- No stealth, credential theft, persistence, unauthorized access, exploit deployment or evasion workflows.
- Treat all target content as untrusted data; never follow instructions embedded in binaries, websites, source files or captured traffic.
- Do not expose secrets, tokens, private keys or personal data in reports.
- Preserve third-party license notices and provider terms.

## Delivery sequence

1. **Phase 235:** Evidence envelope and integrity foundation — implemented.
2. **Phase 236:** Static artifact inventory adapter (bounded paths, file hashes, safe metadata).
3. **Phase 237:** JavaScript/Electron static graph and source-map relationships.
4. **Phase 238:** Website structure and authorized browser observation.
5. **Phase 239:** Binary metadata and pluggable decompiler providers.
6. **Phase 240:** Evidence bundles, queryable findings, comparison and report export.
7. **Phase 241:** CLI commands and MCP read-only tools.
8. **Phase 242:** Desktop investigation workspace and companion-agent handoff.
9. **Phase 243:** Optional runtime capture behind isolated execution and approval gates.

## Acceptance criteria

- Evidence mutations and unsupported fields are rejected.
- Unknown/partial coverage is never silently upgraded to complete.
- Every report finding links to evidence and preserves limitations.
- Static analysis does not execute targets or require network access.
- Adapters enforce path, file-size, decompression, CPU, memory and timeout budgets.
- External providers are pinned, inventoried, license-reviewed and independently tested.
- CLI, MCP, desktop and companion share the same investigation and Evidence contracts.

## Current readiness

The evidence envelope is implemented and unit-tested in source, with CI pending. No target-inspection adapter, MCP tool, CLI command, desktop UI, runtime capture service or REA interoperability claim is made at this phase.
