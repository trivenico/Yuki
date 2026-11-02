# Local Hugging Face model runtime

Yuki supports an optional local Transformers provider and a review-first pinned-revision model snapshot downloader.

## Install

Create a dedicated virtual environment for model inference/training. Install a PyTorch build matching the actual host from https://pytorch.org/get-started/locally/ and then install `requirements-training.txt`. Do not add the training stack to the default CLI/Desktop requirements.

## Download a reviewed checkpoint

First inspect the model card, license, provenance, architecture, tokenizer, context length, quantization and hardware requirements. Select an immutable 40-character commit hash from the Hub, not `main`, `latest` or another mutable branch.

```python
import os
from providers.huggingface_download import download_model_snapshot

report = download_model_snapshot(
    "organization/model",
    "0123456789abcdef0123456789abcdef01234567",
    "~/.cache/yuki/models",
    max_bytes=20 * 1024**3,
    token=os.environ.get("HF_TOKEN"),
)
print(report)
```

The downloader allows only configuration/tokenizer assets and safetensors weights, checks Hub file sizes and available disk space against the configured budget before transfer, requires safetensors, rechecks the resulting snapshot size, and records SHA-256 digests for downloaded weight files. These local digests support later integrity checks; they are not an independent upstream signature verification. It does not activate a model or execute remote code. It is not a license approval or malware scanner.

## Configure local inference

```bash
export YUKI_HF_LOCAL_MODEL_PATH="$HOME/.cache/yuki/models/<snapshot-directory>"
export YUKI_HF_DEVICE=auto
```

Configure `provider: huggingface_local` using Yuki's normal provider configuration, or construct it directly:

```python
from providers.huggingface_local import HuggingFaceLocalProvider

provider = HuggingFaceLocalProvider(
    model_path="~/.cache/yuki/models/<snapshot-directory>",
    device="auto",
    max_new_tokens=512,
)
answer = provider.generate("Explain how Yuki routes model requests.")
```

The adapter requires a local directory with `config.json` and at least one `.safetensors` file. It uses `local_files_only=True`, `trust_remote_code=False`, `use_safetensors=True`, bounded prompt/output sizes and deterministic generation. First generation loads weights into memory; construction does not. Device auto uses CUDA when available and otherwise CPU.

## Important limitations

- This initial adapter supports causal text-generation models; encoder-only, seq2seq, vision, speech and model-specific architectures need dedicated adapters.
- Large models may not fit local RAM/VRAM. The adapter does not automatically quantize weights.
- Model compatibility and hardware fit must be tested on the target host.
- Model metadata and a successful download do not establish legal permission, model safety or quality.
- Keep model artifacts outside the source repository and never commit weights, tokens or user data.
- No model is automatically selected or activated after download.

## Tests

`tests/test_huggingface_local.py` covers input validation, local checkpoint requirements, provider-factory wiring and safe catalog reporting. It does not download weights or require PyTorch/Transformers at test time.
