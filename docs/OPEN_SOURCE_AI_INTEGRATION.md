# Open-source models and inference frameworks

## Strategy

Yuki integrates model frameworks as external runtimes and uses their stable APIs. We do not vendor entire upstream repositories into Yuki. Runtime dependencies, GPU stacks and upstream release cadence are isolated from the base CLI/Desktop installation.

## Supported inference engines

| Runtime | Upstream | Yuki integration |
|---|---|---|
| Ollama | https://github.com/ollama/ollama | Existing native Ollama adapter |
| llama.cpp | https://github.com/ggml-org/llama.cpp | Local OpenAI-compatible endpoint |
| vLLM | https://github.com/vllm-project/vllm | Local OpenAI-compatible endpoint |
| SGLang | https://github.com/sgl-project/sglang | Local OpenAI-compatible endpoint |
| Hugging Face TGI | https://github.com/huggingface/text-generation-inference | Use only releases/configurations whose OpenAI-compatible API has been verified |
| MLX-LM | https://github.com/ml-explore/mlx-lm | Local server mode where supported; Apple Silicon |

The new `LocalOpenAICompatibleProvider` is a shared adapter for local loopback endpoints. It requires a loopback address, refuses remote endpoints and redirects, bounds prompts and responses, and optionally supports a local server API key. It uses the OpenAI chat-completions wire format; it does not imply identical feature support across engines.

## Quick-start server examples

Install each runtime in its own environment and follow its official hardware-specific instructions. Examples (verify the current CLI flags against the upstream release you install):

```bash
# llama.cpp
llama-server -m /path/to/model.gguf --host 127.0.0.1 --port 8080

# vLLM
vllm serve organization/model --host 127.0.0.1 --port 8000

# SGLang
python -m sglang.launch_server --model-path organization/model --host 127.0.0.1 --port 30000
```

Configure Yuki:

```bash
YUKI_PROVIDER=local_openai_compatible
YUKI_LOCAL_OPENAI_MODEL=served-model
YUKI_LOCAL_OPENAI_BASE_URL=http://127.0.0.1:8000/v1
```

If the selected server requires a local key, set `YUKI_LOCAL_OPENAI_API_KEY` in the worker environment. Bind inference servers to loopback unless a separately secured network gateway is intentionally configured. Never expose an unauthenticated model server to the public internet.

## Seed model families

The `models/open_source_catalog.py` file lists starter discovery targets across Qwen, DeepSeek, Mistral, Gemma, Llama, Phi, OLMo and gpt-oss. It is a discovery seed, not an assertion that all checkpoints are OSI open source, commercially unrestricted, available in every region, or compatible with every runtime.

For every exact checkpoint/revision, Yuki must retrieve and inspect the model card and license, access restrictions, acceptable-use terms, supported architecture, tokenizer, quantization, hardware requirements and provenance. Some models are open-weight rather than fully open-source under the OSI Open Source AI Definition. No family-level license assumption is permitted.

## Other reusable open-source AI frameworks

- Transformers + Datasets + Tokenizers + Hub: model loading and dataset pipelines.
- PEFT + Accelerate + TRL: parameter-efficient fine-tuning and preference training.
- smolagents: optional agent framework compatibility spike; Yuki's authorization and sandbox remain authoritative.
- sentence-transformers or FlagEmbedding: embedding/reranking behind a dedicated retrieval interface.
- vLLM / SGLang / llama.cpp / MLX-LM: inference serving.
- ComfyUI / Diffusers: isolated image-generation workflow; never mix model-serving and arbitrary workflow execution privileges.

Adopt frameworks behind Yuki-owned interfaces. Pin versions, isolate environments, create SBOMs, run license/security scans and retain upstream notices when vendoring any code.

## Model families to evaluate

Qwen, DeepSeek, Mistral, Gemma, Llama, Phi, OLMo and gpt-oss are initial candidate families. Selection must be benchmark-driven by task and target hardware. Do not claim Yuki equals frontier hosted systems merely because a checkpoint is installed; evaluate coding, reasoning, multilingual quality, tool correctness, latency, reliability and prompt-injection resistance on Yuki's own benchmark suite.
