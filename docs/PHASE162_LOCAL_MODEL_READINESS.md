# Phase 162 — Local model readiness assessment

## Scope

Extend the existing read-only Ollama inventory with a deterministic resource-fit assessment. The caller supplies a current available-memory snapshot; Yuki does not silently collect machine telemetry or assume that a model will fit.

## Assessment

- Estimate host memory from installed model size using the existing conservative overhead heuristic.
- Apply explicit additional headroom before reporting a plausible fit.
- Evaluate GPU memory only when a GPU offload ratio and measured available GPU memory are supplied.
- Report unknown GPU capacity instead of assuming a GPU exists or has enough memory.
- Distinguish host-memory insufficiency, GPU-memory insufficiency, unknown GPU capacity, and a plausible fit.

## Safety and limitations

- A plausible fit is not a runtime verification or a guarantee that Ollama can load the model.
- Estimates do not fully account for context length, KV cache, concurrent requests, allocator fragmentation, driver/runtime overhead, or other processes.
- No hardware probing, model downloads, pulls, activation, deletion, or provider switching is performed.
- Download, install, and activation must remain separate explicit approval workflows.
- Hardware snapshots must be gathered by a trusted local diagnostics layer and should not be accepted from untrusted remote agents as authoritative.

## Validation

Deterministic unit tests cover host headroom, insufficient host memory, unknown GPU capacity, insufficient supplied GPU capacity, and invalid snapshots. Tests require no Ollama server, GPU, or network.
