# Yuki-Core local inference runtime

The training branch now includes a bounded local inference loader for Yuki-Core's first-party Safetensors releases.

## Load a release

```python
from yuki_training.runtime import YukiCoreRuntime

runtime = YukiCoreRuntime("artifacts/yuki-core/0.1.0", device="cpu")
print(runtime.generate("Hello, Yuki", max_new_tokens=64))
```

The loader verifies the manifest and every artifact SHA-256 before loading. It accepts only the built-in `yuki_decoder_v1` architecture, the Yuki UTF-8 byte tokenizer, and Safetensors weights with matching metadata. It constructs the model using trusted repository code and strict state-dict loading; model files cannot provide Python code. Generation is greedy, local, tool-disabled, and bounded to 1,024 new bytes.

Install the optional dependencies with `pip install -e '.[training]'`. CUDA can be selected only when available. No model download, cloud fallback, tool execution, or network access occurs in this runtime.

## Limitations

This is an initial local inference path for a tiny byte-level training prototype. It is not yet a capable general assistant, production model server, streaming endpoint, quantized runtime, or multimodal model. Production promotion still requires signed release manifests, independent provenance and license review, model-quality and safety evaluations, resource limits at the service boundary, performance testing, and a maintained deployment lifecycle.

## Application integration boundary

When this runtime is exposed through an authenticated multi-user API, compose it
inside an owner-scoped execution context. The API must derive the owner identity
from its authenticated session and supply the corresponding guarded execution
loop; request payloads must never select a model runtime, provider endpoint, or
tool authority. Keep this low-level local inference runtime separate from
tenant authorization and tool execution.

## Select Yuki-Core as an application provider

The optional provider adapter connects a verified release to Yuki's existing
provider and conversation interfaces. It is selected explicitly; it does not
replace the default provider or silently activate an experimental checkpoint.

```yaml
provider: yuki-core
yuki_core_model_dir: ./artifacts/yuki-core/0.1.0
yuki_core_device: cpu
```

Equivalent environment overrides are `YUKI_PROVIDER=yuki-core`,
`YUKI_CORE_MODEL_DIR=/absolute/path/to/release`, and
`YUKI_CORE_DEVICE=cpu` (or `cuda`). Install the optional runtime dependencies
with `pip install -e '.[training]'`. The release directory must already exist
and pass the runtime's manifest and artifact verification.

The adapter accepts text conversations only. It rejects tool definitions and
required-tool requests, and it does not implement streaming, vision, audio,
structured JSON guarantees, or agentic reasoning. This prototype is useful for
testing Yuki's first-party model lifecycle and provider integration, not as the
primary everyday assistant model.