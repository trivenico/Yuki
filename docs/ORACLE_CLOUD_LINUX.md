# Running Yuki on Oracle Cloud Linux

## What auto-detection does

Yuki's hardware profiler detects Linux CPU identity/count, total and available RAM from procfs, free disk space, and NVIDIA GPU name/VRAM when `nvidia-smi` is installed and usable. GPU detection is best-effort. It does not install drivers, alter the VM, or execute privileged commands.

When no model is explicitly configured, the Ollama adapter inspects the installed model inventory and uses model size metadata plus available system RAM to choose a conservative candidate. It reserves memory for the OS/runtime and includes additional weight-memory headroom. This is a heuristic, not a guarantee: context length, KV cache, Ollama overhead, concurrent tasks, and actual quantization/runtime behavior affect memory. It does not pull/download models automatically.

An explicit `ollama_model` selection takes precedence and fails closed if that model is not installed. The hardware profile can be inspected with:

```bash
yuki --hardware
yuki --doctor
```

## Oracle Cloud shape considerations

- Start with a small CPU VM only for testing the control plane and lightweight quantized models. CPU inference can be slow.
- Check the selected shape's actual RAM and CPU allocation; a shape name alone does not guarantee sufficient memory for a given model.
- GPU availability depends on the exact OCI region, tenancy quota, and selected GPU shape. Do not assume an ordinary VM has a GPU.
- Arm/Ampere and x86 shapes can differ in package/wheel support and performance; verify the exact OS image and Python dependencies before production deployment.
- Keep enough disk for the OS, Python environment, model weights, logs, and backups. Model weights can consume several gigabytes each.

## Safe deployment sequence

1. Create a supported Oracle Linux or Ubuntu VM and apply OS security updates.
2. Install Python and Ollama using their official installation instructions. For GPU use, install and validate the supported driver/runtime for the selected OCI GPU shape.
3. Pull a model deliberately after checking the VM's RAM/disk budget; Yuki does not download models automatically.
4. Install Yuki in a virtual environment and run `yuki --check-config`, `yuki --hardware`, `yuki --doctor`, and the repository test suite.
5. Keep Ollama bound to loopback and do not expose TCP 11434 to the internet.
6. For Android access, use the mobile API only over trusted HTTPS and preferably a VPN/private network. Configure host firewall/security lists to allow only the intended source. Never open the mobile port globally just to make phone access work.
7. Add a systemd unit only after the deployment configuration, file ownership, secrets, logs, restart behavior, and shutdown handling have been reviewed.

## Limitations and acceptance gates

- Hardware detection and model-fit estimation are initial support, not benchmarking or guaranteed optimal model selection.
- NVIDIA is the only GPU vendor queried in this phase; other accelerator types may be missed.
- Non-Linux RAM detection requires optional `psutil`; the Linux Oracle path uses procfs without an extra dependency.
- CPU-only models may still be too slow for interactive voice or multi-agent workloads even when they fit in RAM.
- Production deployment still requires validation on the actual OCI shape, CI green on the target commit, and a security review of network exposure and the mobile authentication lifecycle.
