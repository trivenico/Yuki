# Yuki external AI device: reference architecture

Assessment date: 2026-11-03

## Feasibility

Yes. A dedicated external compute device for Yuki is feasible. The practical first product should be a small always-on AI node or mini-PC that runs local inference and exposes a private authenticated API to Yuki clients. A custom accelerator board is possible later, but is substantially more expensive and riskier than starting with commodity compute.

An external device is not automatically a plug-and-play "GPU over USB". The connection transports requests and responses; the model normally executes on the external node's CPU/GPU/NPU. External GPU enclosures are a separate option and require host, firmware, driver and interface compatibility.

## Three hardware approaches

### A. Dedicated AI mini-PC / small workstation — recommended first

- x86-64 CPU with modern vector instructions, ample RAM, NVMe storage, and a supported NVIDIA GPU or AMD GPU where the chosen runtime supports it.
- Linux is a practical initial server OS; Windows can be evaluated as a secondary target.
- Ethernet is preferred for sustained inference traffic; USB-C can provide power/peripheral convenience but should not be assumed to carry high-bandwidth GPU memory access.
- The node runs the inference backend and a Yuki Device Agent service.
- Laptop/desktop connects over the local network; the node can also operate headless.

Advantages: modular upgrades, mature drivers, replaceable GPU/storage, easier cooling and serviceability.

### B. ARM edge device / NPU board

- Candidate classes include Qualcomm Snapdragon-based devices, RK3588-class boards, and Raspberry Pi-class hardware.
- SmallThinker's upstream repository documents experiments and benchmarks on several constrained ARM targets, including RK3588 and Raspberry Pi 5; these must be reproduced with the exact model/runtime version.
- Excellent for low-power wake-word, speech, compact models, sensors and specialist agents.
- Large-model performance, memory bandwidth, NPU SDK maturity and supported operators are the main constraints.

Best as a low-power satellite node, not the only high-capability brain initially.

### C. External GPU enclosure

- A desktop GPU can be placed in a compatible Thunderbolt/USB4 or PCIe expansion enclosure.
- This is not universally compatible: the computer must support the required PCIe tunnelling/interface, firmware and OS drivers; bandwidth is lower than a direct internal PCIe slot and enclosure power/cooling matter.
- This can be an upgrade for a compatible laptop, but it is not the best universal Yuki device.

## Proposed topology

Laptop / desktop / mobile client
  -> authenticated local network or direct USB Ethernet
  -> Yuki Device Agent (pairing, authorization, health, job queue)
  -> inference scheduler (resource limits, model residency, admission control)
  -> backend adapters (Ollama / llama.cpp / optional PowerInfer / future NPU runtime)
  -> CPU / GPU / NPU + local model storage

Yuki's central orchestration, user identity, memory policy, approval system and audit log remain on the trusted host or are deliberately replicated with encryption and explicit sync policy. The external device receives only the prompt/context required for the approved inference request.

## Security and reliability requirements

- Device pairing with a one-time user-approved code and mutually authenticated TLS for network use.
- No unauthenticated listening service; loopback-only until pairing is complete.
- Per-device identity, revocation, key rotation and least-privilege API scopes.
- Explicit data residency policy: prompts may contain private memory; show whether processing is local to the host or sent to a separate device.
- Signed updates, verified runtime binaries, model provenance/license records and rollback.
- Resource quotas, queue limits, request deadlines, cancellation and thermal throttling.
- Encrypted model/data storage where sensitive user data is stored; never store credentials in model directories.
- Offline mode and graceful fallback to the host's local model; no cloud fallback without separate consent.

## Staged build plan

1. Software prototype: run a supported inference server on a second existing PC; connect via authenticated LAN.
2. Device Agent: pairing, health, capabilities, model inventory, queue and stop/unload.
3. Benchmark: compare local-host inference versus remote-node inference, including network overhead and power.
4. Dedicated hardware: select mini-PC/GPU/ARM board only after workload and budget are known.
5. Custom enclosure/PCB: only after thermal, power, EMI, serviceability and production-volume requirements are measured.

## Decisions to defer

Do not select a GPU, NPU, SoC, enclosure, power supply or board until the target model size, sustained tokens/s, concurrency, budget, noise, power and portability requirements are measured. A model's parameter count alone is not enough; quantization, context/KV cache, architecture, memory bandwidth and runtime support materially change sizing.
