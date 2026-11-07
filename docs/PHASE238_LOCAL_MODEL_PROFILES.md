# Phase 238 — Provenance-Gated Local Model Profiles

**Status:** Profile validation and catalog admission implemented; CI verification pending.

## Why this phase exists

Yuki already has a model artifact manifest, model approval lifecycle, durable SQLite catalog, hardware snapshot, CPU-first execution profile and local-device readiness report. Phase 237 introduced an advisory planner but accepted caller-supplied resource requirements. Phase 238 closes that trust gap by requiring model profiles to match an independently reviewed catalog record before they are considered eligible for runtime preflight.

## New module

`providers/local_model_profile.py` provides:
- Strict versioned model profile validation.
- SHA-256 identity for canonical profile metadata.
- Source URL, license, format, quantization and runtime declarations.
- Minimum/recommended RAM, artifact size and disk requirements.
- Explicit OS and CPU architecture compatibility.
- Context-window metadata.
- GPU requirement and minimum VRAM handling.
- Admission only for a matching approved/staged/active catalog artifact with verified provenance, verified license and passing evaluation.

The profile's artifact digest and license must exactly match the catalog. Unknown fields, unsafe source URLs, inconsistent resource limits, unsupported formats and invalid metadata are rejected.

## Safety boundary

This module does not fetch URLs, install runtimes, download weights, probe GPUs, load models or change catalog state. The caller must verify artifact bytes before runtime loading. GPU admission requires a trusted external probe result; this module does not create one.

## Colibri portability strategy

The local model catalog is independent of Colibri, Ollama or any single inference backend. Runtime adapters consume approved profiles, then perform backend-specific preflight and actual load checks. Colibri remains an optional engine integration, not Yuki's source of truth for identity, memory, agent orchestration or policy.

## Tests

Regression coverage includes profile schema, canonical digest, unsafe URLs, unknown fields, resource inconsistencies, catalog approval/provenance, digest/license mismatch, failed evaluation and GPU VRAM admission.

## Not yet delivered

No curated model download catalog, GPU probe, downloader, Colibri adapter, runtime installation, model activation, CLI command or desktop workflow is included in this phase. Those remain separate milestones and require exact upstream model metadata and artifact provenance.
