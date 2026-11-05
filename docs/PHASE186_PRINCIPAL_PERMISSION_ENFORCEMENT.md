# Phase 186 — Enforce principal permissions during specialist delegation

## Security boundary

Specialist manifests already declared the permissions they can use, and plans already recorded each subtask's required permissions. Previously, routing checked that the selected specialist declared those permissions, but did not independently prove that the requesting principal was currently authorized to delegate them.

## Changes

- Planning now requires the host authorization callback to grant each required permission using the scoped action `agent:permission:<permission>`, with the subtask ID as resource.
- Dispatch re-checks both the current specialist manifest and the principal's current grants immediately before task creation.
- If permissions have been revoked or the specialist manifest no longer supports the required permissions, the pending subtask is blocked and a metadata-only `subtask.permission_revoked` audit event is recorded.
- Existing capability routing, human approval defaults, and worker execution through TaskRuntime remain unchanged.

## Host integration contract

The host application's trusted authorization callback must explicitly implement `agent:permission:<permission>` decisions. These are not automatically granted by the coordinator. The coordinator fails closed if the callback denies or raises.

This is a delegation-time authorization check, not a complete process/container sandbox or a guarantee against permission changes in a remote system after dispatch. Tool adapters must still enforce their own authorization at the point of use.

## Tests

Regression coverage verifies denial when the principal lacks a required permission and blocking without task creation when a previously granted permission is revoked before dispatch.
