# Docker CLI executable resolution and host process boundary audit

**Review date:** 2026-11-28  
**Branch:** `research/reference-projects-integration-assessment`

## Finding: the Docker executable is selected through inherited PATH

`core/coding_task_runner.py` invokes the bare executable name `docker` in both `subprocess.Popen()` and `subprocess.run()`. Its `_docker_environment()` helper forwards `PATH` from the current process environment. Consequently, executable lookup is influenced by the inherited PATH rather than an explicitly selected, trusted executable.

If the service environment or a caller-controlled process environment can influence PATH, a different executable named `docker` earlier in the search path may receive the runner's launch or cleanup arguments. That executable runs with the Yuki host process privileges, outside the container's isolation controls. The source does not establish that an attacker can currently modify the service environment; exploitability depends on deployment environment ownership and process boundary.

## Impact and scope

This is a host-side executable trust boundary. Docker container flags cannot constrain a substituted host executable because executable resolution and process startup occur on the host. The same issue applies to cleanup, which independently resolves `docker`.

## Required remediation

1. Resolve the Docker CLI executable from a trusted, administrator-controlled configuration during service startup.
2. Require an absolute canonical path, validate file type/ownership/permissions and reject symlinks or writable parent directories according to the deployment threat model.
3. Use that absolute executable path for every Docker operation; do not rely on PATH lookup.
4. Capture and bind executable identity (for example, verified file identity and digest) into the immutable execution plan and audit record.
5. Ensure the executable cannot be replaced between verification and use; use deployment immutability or an appropriate open/exec strategy for the host platform.
6. Add tests proving malicious PATH entries cannot alter launch or cleanup executable selection, and that missing or untrusted binary configuration fails before approval.

No runtime code changed, no host executable was invoked, no Docker daemon was contacted, no container was created or removed, and no local tests were run.
