# Legacy plugin discovery execution-boundary audit

Date: 2026-11-28

## Reachability and gates

The legacy terminal builds its skill registry through `SkillRegistry.defaults()`.
Plugin discovery is controlled by the configuration flag
`plugin_discovery_enabled` and an explicit `trusted_plugin_modules` list.
The loader rejects wildcard module names and imports only exact trusted module
names found in the configured package. The configuration defaults discovery off.

## Security boundary

The loader's own module documentation states that importing Python executes
code with Yuki's privileges and is not a sandbox. The explicit module list is
a trust decision, not isolation: once imported, a plugin can exercise the
process's filesystem, environment, network, and other available privileges.
Marking every loaded plugin as requiring invocation approval is a separate
application-level guard; it does not contain module-import side effects or
limit what imported module code can do.

The configuration loader validates module-name strings but does not establish
a signed/immutable plugin artifact, source provenance, integrity pin, or
OS-level execution boundary. The repository therefore supports only the
narrow claim that discovery is opt-in and exact-name allowlisted—not that
trusted plugins are sandboxed or safe to import.

## Decision

No runtime changes were made. Keep discovery disabled unless the operator
intentionally trusts each module's code and its dependencies. Production-grade
plugin isolation requires a separate process/container boundary with a
restricted filesystem, credentials, network, and IPC contract; a Python
import allowlist or invocation approval is not a substitute.

No plugin was imported, executed, or installed during this audit. No local
tests were run.
