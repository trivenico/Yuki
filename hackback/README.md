# Security Lab Engine

The directory name `hackback/` is retained for compatibility with the requested layout, but the supported purpose is **authorized defensive lab work only**.

## Implemented modules

- `sandbox.py`: opt-in Docker runner; disabled by default; exact digest-pinned image allow-list; one approval per run; no shell invocation by the host process; resource limits and no network by default.
- `recon.py`: limited Nmap TCP inventory; disabled by default; requires a private literal IP in an explicit target allow-list and approval per scan.
- `exploit_runner.py`: fail-closed placeholder. Metasploit module execution is not implemented and every execution request is rejected.

## Required operating rules

Only test assets you own, explicitly authorized targets, or intentionally vulnerable local labs. A private IP is not proof of authorization. Set a precise target allow-list before enabling a scan. Do not implement hack-back, retaliation, persistence, credential theft, or unrestricted exploitation. Containerization is not a complete security boundary; use a disposable host and do not mount secrets or the Docker socket.

No security module is enabled from the main CLI by default.
