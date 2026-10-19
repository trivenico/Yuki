# Bounded subprocess execution — Phase 19

## Implemented

- Executes only a pre-registered argument vector; never invokes a shell.
- JSON input is validated for serialization and capped before process creation.
- Separate stdout/stderr drain threads enforce per-stream byte caps while the child runs, rather than after unbounded capture.
- Enforces a monotonic wall-clock deadline and terminates the process on timeout or output overflow.
- On POSIX, starts a new session and kills the process group. On Windows, creates a process group and kills the direct child; robust descendant termination still requires a Windows Job Object implementation.
- Returns only a parsed JSON object. Error messages avoid exposing child stderr or payload data.
- Tests cover normal IPC, oversized output, timeout, oversized input, and shell-string rejection.

## Security limitations

This is resource bounding, not a complete sandbox. It does not isolate filesystem, network, credentials, environment variables, CPU scheduling, or syscall access. A child can still access resources available to the Yuki process. Windows descendant cleanup is not guaranteed. POSIX process-group kill does not protect against a child deliberately escaping its process group. A timeout cannot guarantee rollback of external side effects.

Only trusted, reviewed, pre-registered commands should be enabled until OS sandboxing is configured (for example, a dedicated unprivileged account/container with no secrets, read-only filesystem where possible, restricted network egress, cgroup/job-object resource limits, and explicit writable workspace mounts).

## Release gate

Run the complete test suite on the exact PR head, test on supported operating systems, and verify process-tree cleanup and output limits under load. Do not merge this phase or label the system production-ready based only on the new unit tests.
