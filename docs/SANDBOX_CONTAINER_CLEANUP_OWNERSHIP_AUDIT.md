# Sandbox container cleanup ownership audit

**Review date:** 2026-11-28  
**Branch:** `research/reference-projects-integration-assessment`

## Finding: cleanup force-removes by name without proving container ownership

`run_sandboxed_task()` generates a random `yuki-task-<token>` name and inserts it into the Docker run argv. In its `finally` block it calls `_kill_container(container_name)`. That helper executes `docker rm --force <name>` and treats exit code zero as confirmation of cleanup.

The cleanup path does not first resolve and verify a container ID, nor does it verify an attempt-specific label/owner token or daemon identity before deletion. A Docker run that fails because the requested name is already occupied can still reach the runner's cleanup block; the cleanup helper may then force-remove the pre-existing container with that name. A concurrent actor able to create containers on the same daemon could also race name allocation and launch.

The random 96-bit name makes accidental collision very unlikely, but randomness is not an ownership proof. The source review does not establish that this race has occurred or that untrusted actors have Docker-daemon access.

## Impact and scope

This is a potential unintended container deletion / cleanup ownership gap on a shared Docker daemon. The cleanup result proves only that a container with the requested name was removed, not that it was the container created by this attempt.

## Required remediation

1. Use a daemon-supported create operation that returns a container ID before start; do not rely on a name as identity.
2. Attach an unpredictable attempt token and fixed Yuki ownership labels at creation.
3. Before stop/remove, inspect the exact container ID on the same verified daemon and require matching ownership labels and attempt token.
4. If creation fails due to name conflict or ownership cannot be established, do not remove by name; report a bounded cleanup-uncertain outcome.
5. Persist the container ID, daemon identity and attempt ID as execution evidence so recovery can reconcile safely.
6. Add tests for pre-existing name collision, concurrent name claim, mismatched labels, daemon mismatch and cleanup of only the exact owned container.

No runtime code changed, no Docker daemon was contacted, no container was created or removed, and no local tests were run.
