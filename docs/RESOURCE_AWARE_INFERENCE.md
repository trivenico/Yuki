# Resource-aware local inference

Yuki's resource planner is advisory. It never downloads, loads, activates, or
dispatches a model.

## Inputs

- Installed model size and metadata from the read-only Ollama inventory.
- Host total and available RAM from `core.resource_budget.detect_host_memory()`.
- Optional per-GPU free VRAM from a trusted local driver/runtime probe.
- An explicit GPU offload ratio when evaluating GPU or hybrid placement.

Example:

```python
from core.resource_budget import (
    GPUResource, HardwareSnapshot, plan_model_placement, detect_host_memory,
)
from providers.ollama_model_inventory import OllamaModelInventory

models = OllamaModelInventory().list_installed()
host = detect_host_memory()
hardware = HardwareSnapshot(
    total_memory_bytes=host["total_memory_bytes"],
    available_memory_bytes=host["available_memory_bytes"],
    # Populate only from a real driver/runtime query. Do not guess VRAM.
    gpus=(),
)
plan = plan_model_placement(models[0], hardware)
print(plan)
```

The default policy reserves 2 GiB for the host and 512 MiB on each GPU, then
adds runtime overhead and a safety margin to installed artifact size. These
defaults are conservative starting points, not guarantees.

## Placement semantics

- CPU is recommended when the host budget fits and no feasible explicitly
  requested accelerator plan takes precedence.
- GPU/hybrid options are considered only with a caller-supplied offload ratio
  and real free-VRAM telemetry.
- CPU remains a fallback if an explicit GPU request cannot fit.
- No plan is considered proof that a model will load successfully.

Installed file size does not predict KV-cache growth, context length, allocator
fragmentation, backend workspace, concurrent sessions, or actual quantization
behavior. A production scheduler must add runtime-specific measurements,
bounded concurrency, post-load telemetry, and rollback before automatic
placement can be considered. Yuki does not yet have an NVIDIA/AMD cross-platform
VRAM probe wired into this planner.
