# In-process local inference

Yuki can run a locally stored GGUF model in-process through the native
llama.cpp runtime. This is an optional text-generation backend, not a
requirement for Yuki's native capability routing, planner, memory, approvals,
or durable task execution.

## Install

Install the optional native runtime extra:

```bash
python -m pip install -e ".[local-inference]"
```

The llama.cpp runtime is compiled native code. Depending on the platform and
hardware, installation may require a supported compiler/toolchain or a
prebuilt wheel. GPU acceleration is optional; CPU inference is the default.

Obtain a GGUF model through a source you trust, review its license and model
card, and place the file in a private local model directory. Yuki does not
download model files and does not contact a model host.

## Configure

Select the provider and an existing local file in Yuki's configuration:

```yaml
provider: llama_cpp
llama_cpp_model_path: /absolute/private/path/model.gguf
llama_cpp_n_ctx: 4096
llama_cpp_n_threads: 8
llama_cpp_n_gpu_layers: 0
llama_cpp_max_tokens: 2048
llama_cpp_temperature: 0.2
```

The model path can instead be supplied through
`YUKI_LLAMA_CPP_MODEL_PATH`. Environment configuration takes precedence.
The provider validates that the path resolves to a regular, non-empty GGUF
file no larger than 100 GiB, rejects a symlink at the configured file path,
and never fetches remote model content.

## Runtime boundary

- llama.cpp is loaded into the Yuki process; no Ollama daemon or HTTP inference
  endpoint is needed.
- Model loading is lazy. Provider construction does not allocate model memory.
- Context, thread count, GPU layers, output tokens and temperature are bounded.
- Inference and streaming are serialized per provider instance.
- The adapter returns text only. It does not accept model-generated tool calls
  or execute tools. Yuki's native capability router, permission checks and
  approval gate remain responsible for actions.
- A missing native runtime or invalid model file fails closed. There is no
  automatic cloud fallback.
- Call `close()` during provider shutdown to release the native model handle.

## What this means for Yuki's own AI

Yuki owns the orchestration, identity, memory, deterministic capability
routing, planning, task scheduling, evaluation, authorization and safety
system. The optional GGUF weights supply broad language generation and
reasoning; they are pretrained assets, not weights created by Yuki. Running
them locally avoids a hosted inference API, but does not make foundation-model
capability possible without pretrained weights or training.

The local provider is an additive backend. Existing native capabilities and
provider integrations remain available independently.
