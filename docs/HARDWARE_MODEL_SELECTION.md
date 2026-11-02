# Hardware detection and model-fit recommendations

Yuki provides a read-only local hardware inventory via `core.hardware.detect_hardware()` and conservative model-size guidance via `core.model_recommendations.recommend_model_profiles()`.

## Usage

```python
from core.hardware import detect_hardware
from core.model_recommendations import recommend_model_profiles

report = detect_hardware(model_cache_path="~/.cache/yuki/models")
profiles = recommend_model_profiles(report, task="coding")
print(report)
print(profiles)
```

Detection uses operating-system memory counters where available, optional psutil fallback, NVIDIA's local `nvidia-smi` executable, and optional PyTorch CUDA/MPS detection. It does not scan the network, collect serial numbers, load a model, or send inventory off-device.

## Recommendation policy

The recommender uses detected GPU VRAM when available, otherwise system RAM. It reports broad parameter-size bands and caveats, not a guarantee that a checkpoint will fit. Actual memory use depends on architecture, weight precision/quantization, context length, KV cache, runtime overhead, offloading and concurrent applications. Storage is checked separately to warn before large downloads.

No model is downloaded, selected, or activated automatically. Before installation, Yuki must review the immutable model revision, model card, license/usage restrictions, file sizes and hardware requirements, then ask the user to approve the operation.

## Limitations

- NVIDIA GPU discovery requires a working `nvidia-smi`; optional PyTorch may expose CUDA or Apple MPS otherwise.
- AMD ROCm, Intel accelerators, Apple unified-memory capacity and vendor-specific telemetry need dedicated platform adapters.
- CPU/RAM values may be unavailable on restricted or unusual systems.
- Recommendation bands are intentionally conservative heuristics, not benchmark results or compatibility guarantees.
