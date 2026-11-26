# Child-Process and Environment Boundary Audit

**Review date:** 2026-11-26  
**Branch:** `research/reference-projects-integration-assessment`  
**Status:** targeted source audit; not an exhaustive repository-wide process inventory.

## Scope and confidence

The GitHub code-search endpoint returned no indexed matches for generic searches such as `subprocess.Popen`, `subprocess.run`, `create_subprocess_exec`, `shell=True`, and `os.system`. This is an indexing limitation, not evidence that those call sites do not exist. The entries below were verified by fetching the named source files directly from this branch. Do not treat this document as proof that all process creation paths have been found.

## Verified process boundaries

| Entry point | Purpose | Executable / arguments | Environment passed | Working directory / I/O | Assessment |
|---|---|---|---|---|---|
| `providers/gemini_cli.py:GeminiCLIProvider.generate` | Invoke the user's installed official Gemini CLI as a model provider | `shutil.which("gemini")`; fixed argv `gemini --sandbox --approval-mode plan`; `shell=False`; prompt over stdin, not argv | `_cli_environment()` copies only an explicit allowlist: executable lookup, user CLI config/auth locations, temp paths, locale/terminal and TLS certificate variables. Does not forward arbitrary `OPENAI_API_KEY`, AWS credentials, database URLs or `GEMINI_API_KEY`. | Fresh temporary working directory; stdin/stdout pipes; stderr discarded; bounded captured output; timeout and process-group termination on POSIX | Good least-environment baseline. The retained `HOME` and user config directories intentionally expose the user's Gemini CLI profile to this child; this is required for the user's own CLI authentication and is a deliberate trust boundary. The CLI may itself invoke tools; sandbox/plan flags are defense-in-depth, not a substitute for OS/network isolation. |
| `core/docker_probe.py:run_docker_info_probe` | Read-only Docker daemon diagnostics | Previously validated absolute Docker CLI path; fixed argv for `docker --host unix://… info --format …`; no shell | Exact minimal mapping: `PATH=os.defpath`, `HOME=<temporary directory>`, `DOCKER_CONFIG=<temporary directory>`. No inherited provider/database/cloud environment. | Fresh temporary cwd and config; stdin closed; stdout/stderr pipes with strict byte caps; short deadline; POSIX process group termination | Strong environment minimization. It deliberately does not inherit the user's Docker config/credentials; diagnostics target only the explicit local Unix socket. Daemon output is self-reported and is not an isolation attestation. |

## Verified adjacent interfaces

- `yuki_cli.py` is the installed `yuki` console entry point. Its inspected command paths construct providers and local conversation services in-process; no subprocess launch was present in the fetched portions. This does not establish that every imported module or optional command is process-free.
- `ui/desktop/launch.py` is the PySide6 lifecycle entry point. The inspected source uses Qt lifecycle/background-worker abstractions; no subprocess creation was identified in the fetched portion. This is not a complete audit of Qt plugins, packaging bootloaders, or imported modules.
- `core/local_cli_runtime.py` composes the conversation runtime and a deny-all tool registry. It does not itself launch child processes.

## Security properties and limits

1. **No global launcher contract is established.** The two verified subprocess sites each construct their own environment policy. Do not infer that other process launchers inherit either policy.
2. **Environment allowlists are not credential isolation by themselves.** Gemini intentionally receives the owner's home/config paths to use the official CLI login. File permissions, OS account isolation, process debugging rights and the CLI's own child-process behavior remain part of the trust boundary.
3. **Executable lookup has a trust dependency.** Gemini resolves `gemini` using the allowlisted `PATH`; the path and executable ownership must be controlled by the owner OS account. Docker diagnostics instead requires an absolute path and validates its metadata immediately before launch.
4. **No shell is used at the verified call sites.** This reduces shell-injection exposure, but does not make the invoked program safe or sandboxed.
5. **Timeouts and bounded pipes are not complete resource isolation.** They do not replace OS-level CPU, memory, process-count, filesystem and network controls.
6. **Secrets must not move into argv.** Provider prompts are sent over stdin. Future launchers must keep tokens, passwords, DSNs and private prompt data out of command-line arguments, logs, exception messages and task metadata.
7. **Desktop packaging remains a separate boundary.** PyInstaller/Qt may spawn helper processes or load native libraries at runtime. This audit did not inspect the complete frozen application process tree or OS-specific bootloader behavior.

## Required next audit pass

Before claiming a complete process inventory, inspect the full branch tree and every direct or wrapped process API use, including:

- `subprocess.Popen/run/call/check_call/check_output`, `os.system`, `os.exec*`, `os.spawn*`, `multiprocessing`, `asyncio.create_subprocess_*`, Qt `QProcess`, Playwright/browser launchers and packaging bootloaders;
- browser workers, sandbox/container runtimes, voice/transcription backends, model runners, external provider CLIs, desktop startup/update helpers, and test/developer scripts;
- all `env=` mappings and any inherited `os.environ` use at process boundaries;
- child process descendants, inherited file descriptors, credentials/config mounts, current directory, PATH resolution and network access.

For each call site, record the owner, executable trust model, argv data classification, environment source/allowlist, cwd, stdin/stdout/stderr handling, timeout/termination/reaping, resource/network/filesystem limits and tests.

## Remediation decision

- Keep the existing Gemini and Docker policies explicit and separately tested.
- Do not introduce a global monkey patch or blindly scrub all process environments.
- If repeated process-launch requirements emerge, add a shared helper only after the complete call-site inventory identifies a stable common contract. Keep provider-specific credential/config projection explicit; do not make the owner's Gemini profile available to unrelated children.
- Add sentinel-secret tests to every new launcher proving that unrelated provider keys, cloud credentials, database URLs and session secrets are absent.
- Treat browser automation and any persistent agent REPL as a separate disposable worker with connection-time egress enforcement, no Yuki database credentials, no ambient cloud credentials and no general host filesystem access.
- No deployment or production service is authorized by this audit.
