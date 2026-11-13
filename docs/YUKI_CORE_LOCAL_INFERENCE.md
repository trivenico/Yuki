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
