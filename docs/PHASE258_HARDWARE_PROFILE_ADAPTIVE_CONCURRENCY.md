# Phase 258 — Cross-platform hardware profile and adaptive worker limit

## Implemented

- Extended `core/readiness.py` with read-only CPU, physical RAM, free workspace-volume disk, and GPU capability reporting.
- Added a bounded NVIDIA probe using the fixed `nvidia-smi` argument list, `shell=False`, and a two-second timeout. Apple Silicon is identified as a possible integrated GPU path without claiming benchmarked acceleration.
- Added conservative worker recommendations based on logical CPU count and physical RAM, always bounded to 1–4 active workers. Missing RAM data falls back to one worker.
- Wired `main.py` local specialist runtime creation to the recommended concurrency.
- Added `scripts/hardware_profile.py` with human-readable and JSON output.
- Added tests for detected hardware fields and RAM-based concurrency tiers.
- Documented the new operator commands and limitations in README.

## Run

```bash
python scripts/hardware_profile.py
python scripts/hardware_profile.py --workspace ./yuki-workspace --json
python -m unittest tests.test_readiness
```

## Safety and limitations

The profile is read-only. It does not install drivers, download models, start Ollama, or benchmark inference. NVIDIA detection depends on `nvidia-smi`; other GPUs may be reported as unknown. Worker recommendations use RAM and logical CPU only and are conservative starting values, not guarantees of model fit. GPU VRAM, model quantization, context length, and model residency still need runtime-aware scheduling.

## Verification status

The code and tests are committed, but they have not been executed in this environment. No CI pass is claimed. Windows/macOS/Linux runtime smoke tests remain outstanding.
