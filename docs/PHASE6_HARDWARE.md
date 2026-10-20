# Phase 6 — Hardware detection and local model selection

## Implemented in the hardening branch

- `core/hardware.py` exposes `detect_hardware()` and a typed, JSON-serializable `HardwareInfo` report.
- Linux memory is read from `/proc/meminfo`; Windows uses `GlobalMemoryStatusEx`; macOS uses `sysctl` and `vm_stat`. Optional `psutil` is a fallback, not a core dependency.
- CPU architecture/count, best-effort CPU label, free disk space, available local inference executables, and best-effort NVIDIA information are included.
- The terminal runtime prints a short inventory and supports `/hardware` for detailed diagnostics.
- When using Ollama with no explicit model configured, `providers/ollama.py` inventories installed models on first generation and chooses the largest model whose reported size fits a conservative RAM budget.
- Model recommendation fails closed when RAM or model-size information is unknown, or no installed model fits the estimate. It never downloads models or invokes cloud fallback.
- Regression tests cover Linux memory parsing, macOS memory parsing, absent optional runtimes, Apple Silicon caveats, model-fit decisions, malformed model inventory, and automatic model selection.

## Current limitations

- NVIDIA detection requires `nvidia-smi`. Other GPU vendors are not comprehensively enumerated.
- Apple Silicon reporting is a platform heuristic; it does not prove Metal support or runtime acceleration.
- Model selection uses reported model size and host RAM only. It does not yet validate context/KV-cache settings, concurrent load, GPU offload, runtime backend support, or real-time inference latency.
- The local executable inventory is not an end-to-end health check. Ollama availability is checked when a model inventory is requested.
- A user-configured model is respected and is not silently replaced; explicit model configuration can still exceed available memory.
- Cross-platform CI and tests on physical Windows, macOS/Apple Silicon, NVIDIA, AMD, and Oracle Cloud hardware remain necessary before claiming universal compatibility.

## Verification status

At commit `280af44a9df5a9a09bb3c2f4fab1d42b211c844b`, the GitHub `Yuki tests` workflow passed **200 unit tests** and the `Security` workflow passed. The production `CI` workflow still failed because repository-wide quality gates remain red: 231 Ruff lint findings, multiple files needing Ruff formatting, six mypy errors in five existing modules, and a failing console-entrypoint smoke check. Therefore Phase 6 is an implementation increment, **not a production-readiness certification**.
