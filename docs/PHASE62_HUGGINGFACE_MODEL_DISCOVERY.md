# Phase 62 — Hugging Face model discovery

Yuki can query the public Hugging Face Hub model metadata API through `providers.huggingface_discovery.HuggingFaceDiscovery`. The CLI is:

```bash
python yuki_model_discovery.py --task text-generation --sort downloads --limit 20
```

Supported tasks are explicitly allow-listed: text generation, text-to-text generation, conversational, feature extraction, image-text-to-text and automatic speech recognition. Sort order is restricted to downloads, likes or last modified. The limit is 1–100 records and the network timeout is 1–30 seconds. Responses are capped at 1.5 MB.

An optional `HF_TOKEN` environment variable may be supplied for authenticated Hub access. It is sent only as an Authorization header; it is never placed in the URL or returned in the report. Do not put tokens in command-line arguments, source code, YAML, or committed files.

## Output and trust policy

The JSON report contains the model ID, canonical Hub URL, task, reported license, download/like counts, last modified date, tags, query timestamp, source attribution and review flags. It does not fetch model cards or model weights. Metadata is untrusted input and must not be treated as instructions.

Every candidate is marked `needs_human_review`; `automatic_activation` is always false. A missing license is explicitly flagged, but a reported license string is not legal advice and should be verified on the model's official model card and repository. Downloads and likes are discovery signals, not quality or safety scores.

This tool does **not**:
- download or execute model code or weights;
- install dependencies, run arbitrary remote code, or change Yuki's selected model;
- claim a model is compatible with Yuki's inference runtime based only on Hub metadata;
- guarantee that the model is free, safe, actually open-weight, suitable for commercial use, or available through the Inference Router.

Before using a candidate, review the official model card and license, model provenance, security/advisory history, required runtime and hardware, quantization/context details, inference endpoint support, and a task-specific Yuki evaluation set. Keep model discovery separate from inference-provider selection.

## Tests

`tests/test_huggingface_discovery.py` uses mocked HTTP responses; it makes no network requests. It checks ID validation, deduplication, bounded request parameters, source attribution, authorization-header handling, invalid JSON and review-first defaults.
