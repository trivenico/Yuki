# PowerInfer and SmallThinker: Yuki adoption assessment

Assessment date: 2026-11-03
Upstream: https://github.com/Tiiny-AI/PowerInfer
License reported by upstream repository metadata: MIT (verify bundled third-party notices before redistribution).

## Executive decision

Adopt an inference-backend abstraction and experimentally evaluate PowerInfer/SmallThinker. Do not make PowerInfer the only runtime or replace the currently supported Ollama/provider paths.

PowerInfer is an inference engine, not a general-purpose agent framework. It does not provide Yuki's identity, consent-aware memory, planning, tool authorization, workflow journal, or companion UX. Yuki should own those layers and treat inference engines as replaceable execution backends.

## What is technically valuable

### 1. Activation-aware sparse inference

The original PowerInfer design exploits activation locality: frequently activated ("hot") neurons can be placed on GPU while less frequently activated ("cold") neurons are computed on CPU, using activation predictors and sparse operators. This can improve the practical model-size/VRAM tradeoff on supported sparse model architectures.

Adoption implication:
- Build an optional backend adapter, not a fork of Yuki's reasoning loop.
- Require explicit model architecture/format compatibility metadata.
- Distinguish true PowerInfer sparse/predictor model artifacts from ordinary GGUF files. Upstream says ordinary llama.cpp weights may run but do not gain the sparse speedup.
- Never promise a speedup without measuring the target hardware and exact model.

### 2. SmallThinker and constrained devices

The repository also contains a newer SmallThinker inference path for sparse MoE models and constrained devices. Its README publishes benchmark tables for x86, Qualcomm-class Android devices, RK3588, Raspberry Pi 5 and other ARM boards. Those numbers are upstream-reported results, not Yuki's independently reproduced benchmarks.

The documented memory-efficient path uses expert bundles and a cache limit. This is a promising research direction for a low-power Yuki node, but its build/runtime requirements and model-specific workflow should be isolated from the stable desktop runtime.

### 3. CPU/GPU hybrid scheduling and memory budgets

PowerInfer's CPU/GPU split and explicit VRAM budget suggest capabilities Yuki should expose independent of a particular engine:
- hardware inventory (CPU ISA, RAM, GPU, VRAM, driver/runtime);
- per-model estimated RAM/VRAM/disk footprint;
- user-defined compute and memory ceilings;
- measured prompt-processing and generation throughput;
- thermal/power and sustained-throughput observations where the platform exposes them;
- safe model unload and resource release.

### 4. Local serving API

PowerInfer's server example demonstrates a local HTTP serving boundary with streaming completion, context and VRAM controls. Yuki can integrate an engine through a localhost-only adapter. Bind to loopback by default; remote access must use authenticated, encrypted transport and explicit user configuration.

## Upstream constraints and caveats

- The root README describes x86-64 AVX2 on Linux/Windows and Apple Silicon CPU-only support; it says macOS performance gains are not significant and describes Metal sparse support as planned.
- The root project documents NVIDIA CUDA and AMD ROCm build paths.
- The SmallThinker subtree has separate build steps and platform-specific paths, including Android NDK and ARM board guidance. Do not infer that every PowerInfer backend supports every SmallThinker target.
- The repository's own model list and formats are architecture-specific. Compatibility must be checked per model, not inferred from a .gguf suffix.
- Published speed comparisons are workload-, model-, quantization-, context-, and hardware-dependent.
- Upstream project status and supported branches can change. Pin a reviewed commit and record it in Yuki's backend manifest before packaging.

## Proposed Yuki integration

Create a backend contract with operations:
- discover() -> backend version, supported platforms, capabilities;
- inspect_model(path) -> architecture, tensor/quantization format, tokenizer/template metadata, license provenance if known;
- estimate_resources(model, device) -> estimates with confidence and assumptions;
- start(model, resource_policy) -> process/service handle;
- health() -> readiness, loaded model, resource use;
- generate(request) -> normalized text/tool-call/usage events;
- stream(request) -> normalized event stream;
- stop()/unload() -> bounded graceful shutdown.

Implement a PowerInfer adapter as an optional backend behind this contract. Keep the provider/model router responsible for policy and selection; keep the agent loop and structured tool protocol responsible for reasoning and tool permissions. The inference engine must never receive tool authority merely because it generated a tool-call proposal.

## Evaluation gates

1. Pin upstream revision and inspect license plus third-party notices.
2. Build in an isolated CI/container environment for one supported platform.
3. Validate model format and tokenizer/template compatibility.
4. Benchmark the same prompts and model on baseline Ollama/llama.cpp and PowerInfer.
5. Record cold start, prompt tokens/s, generation tokens/s, peak RAM/VRAM, CPU/GPU utilization, temperature, failures and output quality.
6. Run long-context, cancellation, malformed-request, process-crash and unload tests.
7. Promote only if a reproducible target-hardware win exists without unacceptable quality or stability regression.

No automatic installation, model download, or runtime switching should occur as a side effect of model discovery.
