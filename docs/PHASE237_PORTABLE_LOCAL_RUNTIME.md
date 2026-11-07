# Phase 237 — Portable Local Runtime Readiness

**Status:** Read-only readiness planner implemented; CI verification pending.

## Colibri lessons

Colibri's deployment workflow is valuable because it separates device detection, model fit, backend selection, installation, runtime lifecycle and API discovery. It supports a guided one-step setup on Windows, Linux and macOS, resumable downloads, CPU-first fallback and an MCP control surface. Yuki adopts these architectural lessons without importing Colibri's engine or assuming every listed model is portable.

Colibri is Apache-2.0. Any future source reuse must retain notices and be reviewed alongside the licenses and terms of model weights, conversion tools, CUDA/Vulkan components and dependencies. Model licenses are not inherited from the engine license.

## Existing Yuki foundation

Yuki already has:
- A cross-platform CPU/RAM snapshot in `providers/hardware_snapshot.py`.
- Conservative CPU-first execution budgets in `providers/device_execution_profile.py`.
- Local Ollama provider integration.
- Provider selection and readiness controls.
- A desktop shell and CLI, with packaged service lifecycle still incomplete.

The new `providers/local_device_readiness.py` combines these foundations with local model-directory free-space checks and a strict caller-supplied model requirements manifest.

## Readiness report

The report describes:
- OS, CPU architecture and logical CPU count.
- Physical and available RAM where the OS exposes them.
- Available disk space in the selected existing model directory.
- Conservative model-runtime memory budget.
- Model compatibility against OS, architecture, RAM and disk requirements.
- Explicit reasons for a rejected model.

GPU is intentionally reported as `not_probed`. A model that requires a GPU is not admitted until Yuki has a trusted GPU probe and runtime-specific compatibility checks. The module never downloads, installs, activates or starts anything.

## Important distinction

The report is advisory, not a model loader or a guarantee of inference performance. A model's true fit depends on architecture, quantization, context length, KV cache, runtime build, GPU backend and concurrent Yuki workload. The runtime must perform final admission before loading weights.

## Next milestones

1. Add a versioned, license-aware model catalog with exact artifact digests and model-specific resource metadata.
2. Add verified CPU/GPU capability probes for Windows, Linux and macOS.
3. Integrate an optional Colibri-compatible local OpenAI API endpoint through the existing provider gateway, with no silent fallback.
4. Add resumable, checksum-verified model downloads with explicit user approval.
5. Package a supervised local service lifecycle for the desktop app and CLI.
6. Add an installation doctor, rollback, logs, status and offline mode.
7. Add a portable companion assistant profile that uses the same local API and permission policy.

## Acceptance criteria

- No network, package installation, model download or runtime activation during readiness inspection.
- Unknown GPU state stays unknown; no inferred VRAM.
- Unknown model requirements are rejected, not guessed.
- Low-memory machines get conservative CPU-only recommendations.
- Model licensing and artifact provenance are tracked independently from engine licensing.
- Windows, Linux and macOS checks run in CI where runners are available.
