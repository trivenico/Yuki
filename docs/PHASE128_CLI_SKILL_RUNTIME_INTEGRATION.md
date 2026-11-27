# Phase 128 — historical CLI skill-runtime proposal

> **Status: historical / not wired into the current installed CLI.**
>
> This document records an earlier proposed CLI skill interface. It is not an
> operational guide for the current `yuki` command and must not be used as
> evidence that CLI skill execution is available.

The current installed CLI is conversation-only. Its parser exposes diagnostics,
provider setup/status/test, one-shot `ask`, persistent `chat`, and local owner
bootstrap. Persistent chat constructs an empty guarded tool registry with a
deny-all authorizer; it explicitly reports that tool execution is disabled.
There are no current `/skill list`, `/skill approve`, or `/skill invoke`
commands in `yuki_cli.py`.

The earlier design described process-local one-shot approvals through
`ApprovedSkillRuntime`. Those notes describe the historical proposal only.
They do not establish an authenticated owner-approval boundary, a production
composition, or an OS isolation boundary. In particular, an approval-granting
method must never be exposed directly to model output, untrusted UI actions, or
untrusted plugins.

## Current security boundary

- The installed CLI conversation runtime uses a deny-all tool registry.
- The primary agent execution path must continue to use its guarded
  `YukiCoreAgent.run_skill()` approval flow.
- In-process Python skills execute with the Yuki process's ambient OS
  privileges; manifest permission declarations are not a sandbox.
- Permissioned third-party skills remain disabled until a separately reviewed
  isolated executor and authenticated owner-approval composition exist.

## Future work required before a CLI skill interface

Any future CLI skill surface requires a separately reviewed design and
implementation covering authenticated owner identity, explicit per-invocation
approval bound to the exact skill and argument digest, revocation semantics,
audit evidence, least-privilege execution isolation, and deterministic
regression tests. Do not restore the historical commands by copying this
document's examples.
