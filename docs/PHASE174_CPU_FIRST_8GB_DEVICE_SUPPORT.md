# Phase 174 — CPU-first and 8 GB device support

## Product requirement

Yuki must remain usable on ordinary laptops and desktops without a dedicated
GPU. CPU inference is a supported execution mode, not an error fallback. GPU
is optional acceleration. A compact device must run a deliberately small,
quantized local model while Yuki's orchestration, memory and essential tools
remain available.

## Delivered policy

`providers/device_execution_profile.py` turns a read-only hardware snapshot
into a conservative execution profile. It uses both physical and currently
available RAM, caps the model/runtime budget, limits context and concurrent
agent tasks, and never requires a GPU. It does not load models or claim a
specific model will fit.

For devices with at most 8 GiB RAM, the initial policy uses:
- Model/runtime budget: no more than 35% of physical RAM and 50% of currently
  available RAM, whichever is lower.
- Recommended context: 2,048 tokens.
- Concurrent agent tasks: one, queued rather than parallel.
- CPU thread cap: two.
- GPU: optional acceleration only.

These are conservative starting heuristics and must be calibrated with
measured runtime peak memory, latency and OS pressure on real hardware.

## Model strategy by device

- 8 GiB CPU-only: small 1B–3B-class quantized models are candidates for
  benchmarking, not guaranteed fits. Start with a compact 1B/1.5B-class model
  and short context; test 3B-class candidates only when measured budgets allow.
- 16 GiB: benchmark compact 3B–7B-class quantized models.
- 32 GiB and above: benchmark larger quantized models and selectively enable
  parallel specialist tasks.
- GPU devices: allow measured layer offload, while preserving CPU-only mode.

Parameter count is not a reliable memory guarantee. Model weights, quantization
format, KV cache, context length, runtime buffers, tokenizer and OS all matter.
Keep an explicit memory reserve and measure peak resident memory.

## Runtime behaviour

1. Detect host RAM and CPU locally; GPU probing is optional and separate.
2. Build a profile from current available RAM, not just installed RAM.
3. Select only an installed, rights-reviewed, digest-verified model with a
   measured compatible runtime profile.
4. Prefer CPU-compatible quantized formats and a supported local runtime.
5. Reduce context, batch size, thread count and concurrent agents under memory
   pressure; pause background tasks before risking an out-of-memory failure.
6. Keep vision/speech as optional queued capabilities on compact devices.
7. Never silently upload prompts, files or memory to cloud services when local
   resources are insufficient. Ask for explicit cloud opt-in.

## Important limitations

The profile is a recommendation engine, not an automatic model downloader or
runtime launcher. It does not promise that every Yuki feature runs concurrently
on 8 GiB. The compact-device product target is essential chat, reasoning,
memory retrieval and a small number of sequential tools; heavier models and
multimodal workloads can be delegated to an explicitly approved remote worker.
