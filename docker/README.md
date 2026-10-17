# Docker runtime

The Dockerfile is a convenience for the guarded terminal runtime, not a guarantee of sandbox security. Review the image and dependencies before building.

Do not mount the host home directory, Docker socket, SSH keys, cloud credentials, or other secrets. Use a dedicated disposable workspace, drop capabilities, use read-only root filesystem where practical, and apply CPU/memory/network limits in the host's container runtime. Do not expose Ollama or other host services to untrusted workloads. No code execution or security scanning is enabled by this container.
