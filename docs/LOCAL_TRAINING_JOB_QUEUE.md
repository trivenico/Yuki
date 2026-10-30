# Local training job queue

The SQLite queue stores explicitly approved LoRA/QLoRA training plans. It requires an exact dataset revision already approved by the active DatasetRegistry, bounds RAM/VRAM/runtime requests, supports pause/resume/cancel, and only claims work when the host reports idle status, allowed power state and sufficient available resources. A lease binds the running job to one worker identity.

## Execution boundary

This is queue and policy infrastructure only. It does not download data, load models, invoke Transformers/PEFT, spawn processes, or change model weights. A future trainer must run in a separate sandbox with dataset integrity checks, network/filesystem restrictions, resource limits, cancellation, checkpointing, artifact verification and evaluation. Never mark a job successful until a verified model artifact and evaluation report are available.

DatasetRegistry approvals are currently in-memory. After restart, approvals must be re-established from trusted review records before scheduling. Persist signed approval evidence before production use.
