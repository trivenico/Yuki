# Local Model Runtime Adapter Contract

Status: proposed architecture contract. No provider integration or hardware acceleration is implemented by this document.

## Purpose

Define the boundary between Yuki's provider/conversation layer and a locally hosted model runtime. The contract is for runtimes that own model weights and tensor execution, such as PyTorch inference on CPU/CUDA and JAX execution on CPU/TPU.

A provider adapter that only sends an HTTP request to Ollama or a cloud API is not a local model runtime adapter. Ollama owns model placement inside its server process; Yuki must use server configuration and server telemetry rather than wrapping the HTTP request in a local CUDA/TPU context. Cloud inference remains remote and must not consume Yuki local accelerator reservations.

## Required adapter lifecycle

A concrete adapter must expose the following operations through a typed, runtime-specific implementation:

1. **Inspect** — validate runtime version, model artifact compatibility, requested backend/device, and available device topology before allocating resources.
2. **Load** — load model weights and place them explicitly on the selected device. Return an opaque handle bound to the adapter instance and the exact model specification.
3. **Prepare inputs** — validate input shape/type/size and transfer tensors to the selected device. Never assume a device context moves existing tensors.
4. **Infer** — execute only against a handle created by that adapter. Bound batch size, input tokens/sequence length, and output size.
5. **Synchronize and collect** — wait for asynchronous device work where required, surface deferred runtime errors, and transfer user-facing outputs to host memory before returning them to provider code.
6. **Unload** — release runtime references and device allocations deterministically; synchronize as required. Cleanup must be idempotent and must not hide the original inference error.
7. **Close** — release adapter-owned runtime resources without closing injected/shared runtimes owned by another component.

The adapter must reject a handle from another adapter, a changed model identity, an unsupported device, or an operation after unload. It must not silently switch devices or runtimes.

## Resource admission and residency

The current ComputeScheduler reserves caller-estimated memory for the duration of one synchronous operation. That is insufficient to account for model weights that remain resident between inference calls.

Before integrating model loading, Yuki must add a **model residency lease** (or an equivalent explicit allocation object) whose reservation remains held from before model load until successful unload. Inference calls use that already-admitted resident model and separately enforce bounded transient-memory/concurrency limits. Releasing a request's transient reservation must never release the model's resident-memory reservation.

The admission path must be:

1. Validate model/runtime/device compatibility without allocating weights.
2. Acquire bounded resident-memory and model-slot admission.
3. Load and place the model.
4. On load failure, release all admission and runtime resources.
5. For each inference, acquire bounded transient-memory/concurrency admission.
6. Execute, synchronize, collect host outputs, and release transient admission in all outcomes.
7. On unload, release model resources first, then release resident admission in a finally-safe path.

All reservations are currently process-local. They do not protect against other processes, external GPU jobs, or another host. CUDA free VRAM is a construction-time snapshot and TPU HBM is operator-configured; neither is a live guarantee. Runtime OOM remains possible even after scheduler admission.

## Failure and recovery policy

- **OOM:** classify runtime-specific out-of-memory failures; stop admitting work to the affected model/device, attempt deterministic cleanup, and mark the model session unhealthy if cleanup or synchronization is uncertain.
- **No automatic retry on another device:** retry only when the adapter can prove the failed operation had no externally visible side effects and the target runtime is explicitly configured.
- **CPU fallback:** opt-in per model and operation. Re-load/validate the model on CPU under a separate CPU residency lease; never pretend that changing a device context migrates model weights.
- **Cancellation:** check before dispatch and at supported runtime boundaries. Do not release residency while asynchronous device work may still reference model memory.
- **Cleanup failure:** preserve the primary inference/load error and record cleanup failure separately; do not report resources as safely released when that is unknown.
- **Concurrency:** serialize non-thread-safe runtime/model operations or expose a documented, tested concurrency capability. Bound queues and reject overload rather than growing unbounded work.

## Runtime-specific obligations

### PyTorch

A CUDA implementation must explicitly load/move the model to the selected `torch.device`, set evaluation/inference mode as appropriate, move tensor inputs, execute under the correct device context, synchronize before reporting completion where required, and return host-safe outputs. It must handle CUDA OOM distinctly and release model/tensor references during unload. Device context alone is not model placement.

### JAX

A TPU implementation must validate the selected JAX device, explicitly place inputs and parameters (for example with device placement APIs), compile/cache only bounded shapes or functions, synchronize asynchronous results before releasing transient admission, and transfer returned values to host memory when required. `jax.default_device` alone does not migrate existing arrays or model parameters.

### Ollama

Ollama remains a server-managed local provider. Yuki's client adapter may inspect configured server/model metadata and server-reported telemetry when available, but it cannot claim device placement or account for server-side VRAM/HBM using Yuki's process-local scheduler. A future integration must define a server-side resource/telemetry contract.

### Cloud providers

OpenAI-compatible and other remote providers remain network providers. Their requests are not wrapped in local CUDA/TPU execution contexts and do not consume local model residency reservations.

## Integration gates

Do not connect this contract to `AgentExecutionLoop` or change provider selection until all gates pass:

- A concrete PyTorch CPU/CUDA adapter and/or JAX CPU/TPU adapter implements the lifecycle above.
- Model residency admission is held across load-to-unload lifetime.
- Input placement, output synchronization/collection, unload, and failure cleanup have deterministic tests with fake runtimes.
- OOM, cancellation, concurrent inference, and adapter/handle mismatch tests pass.
- Hardware-optional tests exercise real CUDA/TPU only when available; absence of hardware is a skip, not a false pass.
- Provider semantics, tool authorization, conversation cancellation, and streaming behavior remain unchanged.
- Capability manifest and user-facing status distinguish implemented adapters from planned or hardware-unverified integrations.

Until then, Yuki's CUDA/TPU support is limited to optional device-context backends and scheduler admission for bounded synchronous operations. End-to-end local model acceleration is not implemented or verified.
