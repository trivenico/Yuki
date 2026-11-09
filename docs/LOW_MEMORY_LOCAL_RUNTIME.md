# Low-memory local runtime

Yuki separates **application memory** (Python, SQLite, browser/API, workers and embeddings) from **model memory** (the Ollama model weights, KV cache and inference workspace). They must be budgeted together. A model that fits in the machine's total RAM may still cause swapping or OOM when Yuki and the operating system are running.

## What we learned from kimi-k3-in-c

The referenced C engine demonstrates four transferable techniques:

1. Keep the frequently used working set resident and stream cold/large data from storage.
2. Make memory allocation an explicit budget rather than allowing caches to grow without bounds.
3. Measure peak RSS, actual bytes read, and output correctness at each budget.
4. Use reproducible memory-ladder tests; do not infer performance from one timing sample.

Its 1.56 TB Kimi K3 checkpoint and roughly 8.24 GB minimum reported peak RSS are not a suitable laptop deployment target. Its throughput at that budget is also very low. Yuki should not attempt to load that checkpoint or reimplement its model-specific C kernels. Yuki already delegates local inference to Ollama; the practical improvement is to constrain Ollama and Yuki's own concurrency.

## Current low-memory controls

Yuki now passes these bounded Ollama options on chat, streaming chat, and direct generation:

| Setting | Default | Accepted range | What it controls |
|---|---:|---:|---|
| `ollama_num_ctx` | 2048 | 256–32768 | Context window and KV-cache footprint |
| `ollama_num_predict` | 512 | 1–8192 | Maximum generated tokens |
| `ollama_num_batch` | 128 | 16–2048 | Prompt-evaluation batch size / temporary working memory |
| `ollama_num_thread` | 0 | 0–256 | CPU threads; zero delegates to Ollama |

Settings can be changed in `config.yaml` or overridden using `YUKI_OLLAMA_NUM_CTX`, `YUKI_OLLAMA_NUM_PREDICT`, `YUKI_OLLAMA_NUM_BATCH`, and `YUKI_OLLAMA_NUM_THREAD`. Invalid values fail configuration instead of being silently accepted.

Example for a small laptop:

```yaml
provider: ollama
ollama_model: your-installed-small-quantized-model
ollama_num_ctx: 1024
ollama_num_predict: 256
ollama_num_batch: 32
ollama_num_thread: 2
```

These values are conservative starting points, not guaranteed RAM ceilings. Ollama/model implementations may reserve additional memory, and GPU offload changes the RAM/VRAM split.

## Hardware starting points

Choose by **available memory after the OS**, not just installed RAM. Model sizes below are rough classes; quantization, architecture, context and runtime version matter.

| Installed RAM | Suggested starting model class | Initial context | Batch | Active specialist workers |
|---:|---|---:|---:|---:|
| 4–8 GB | 1B–3B, Q4 | 1024 | 32 | 1 |
| 8–16 GB | 3B–7B, Q4 | 2048 | 64 | 1 |
| 16–32 GB | 7B–14B, Q4 | 4096 | 128 | 1–2 |
| 32 GB+ | benchmark 14B+ / larger quantized models | 4096+ | 128+ | increase only after measuring |

On a 4 GB machine, a browser UI plus a local model may still be too tight; use a remote inference provider or a smaller dedicated runtime rather than risking swap thrash. On 8 GB, prefer one small quantized model and close other model-serving processes. Do not keep multiple large models resident.

## Yuki-side operating practices

- Keep the default `LocalHashingEmbedder` for low-memory installations. The optional Hugging Face embedder loads a transformer into the Yuki process and can materially increase RAM use.
- Keep specialist concurrency at one on machines below 8 GiB. Yuki's current hardware profile already recommends one worker below 8 GiB and caps local worker concurrency conservatively.
- Avoid running multiple Ollama models or multiple Yuki instances on the same laptop.
- Keep the model store on SSD where possible; do not assume disk paging will provide interactive speed.
- Keep context short by summarizing older conversation history and retrieving only relevant approved memories. Persistent memory storage is not a substitute for a bounded model context.
- Keep browser/desktop tools disabled unless required; each additional process consumes memory.
- Do not disable OS swap as a general tuning trick. Monitor it and reduce context/model size if sustained swapping occurs.

## Measure before recommending

Run:

```bash
python scripts/hardware_profile.py --json
python scripts/models.py --json
python scripts/local_alpha_check.py
```

The inventory is read-only and estimates model weight memory heuristically. It does not prove that a model fits or benchmark inference.

For a valid comparison, record at least:

- machine OS, CPU, physical RAM, GPU/VRAM and storage device;
- exact model name, digest and quantization;
- `num_ctx`, `num_batch`, `num_predict`, thread count and worker count;
- peak process RSS and GPU memory during a representative conversation;
- tokens/second, cold-start time, prompt length and whether swapping occurred;
- answer correctness and tool-call behavior.

Run at least three equivalent trials per configuration, distinguish cold and warm model loads, and compare the full Yuki + Ollama process tree—not only the Python process. A memory configuration is acceptable only if it avoids sustained swapping and preserves functional correctness.

## Not implemented yet

- A measured end-to-end memory budget spanning Yuki, Ollama, model RSS/VRAM, browser and specialist workers.
- Runtime adaptive context/batch settings based on available memory.
- A repeatable cross-platform benchmark harness that records peak process-tree RSS and GPU VRAM.
- Automatic unloading of idle models with a tested lifecycle and race-safe request coordination.

These remain explicit follow-up tasks. The current Ollama settings provide bounded, operator-controlled starting points, not an automatic memory manager or a promise that every model will fit.
