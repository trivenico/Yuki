# Approval credential broker deployment contract

This document describes the production requirements for the durable approval path
implemented by the capability gateway, authenticated approval service, encrypted
credential broker, and durable skill worker.

## Current implementation boundary

The broker accepts injected `seal(plaintext: bytes, aad: bytes) -> bytes` and
`unseal(envelope: bytes, aad: bytes) -> bytes` callables. This is an adapter
boundary, not an encryption implementation. The repository's test fixtures are
deliberately test-only and MUST NOT be used to protect production credentials.

The broker database is created with owner-only POSIX file permissions where
supported. It rejects symlink and non-regular database paths. On Windows, the
deployment must provision and verify restrictive NTFS ACLs. This file-level
protection does not encrypt SQLite pages or replace host-level disk encryption.

## Required production encryption adapter

Before enabling privileged skill execution, inject a reviewed KMS-backed
authenticated-encryption adapter with all of these properties:

- Use a managed KMS/HSM key and a vetted AEAD implementation; do not implement
  cryptography in Yuki or use the test XOR/HMAC fixture.
- Authenticate the complete associated data supplied by the broker. AAD binds
  the request ID, authenticated principal, goal, task, worker identity,
  capability, target, policy version, and expiry.
- The envelope must be versioned and identify the KMS key version/key ID needed
  for decryption and rotation. The adapter must reject unknown versions.
- Decryption must fail closed on authentication failure, AAD mismatch, key
  revocation, KMS outage, or malformed envelope. Never fall back to plaintext.
- Keep KMS credentials outside the repository and database. Grant encryption
  and decryption permissions only to the service/worker identities that need
  them. Log KMS key metadata, never plaintext tokens or envelope contents.
- Define key rotation, old-key retention, revocation, recovery, and backup
  procedures before production rollout.

The adapter must preserve the broker's byte-oriented callable contract. Do not
write a custom cipher or substitute unauthenticated encryption.

## Trusted runtime composition

The production composition root must create one policy engine and one
capability gateway, then inject those same instances into the authenticated
approval service, broker, guarded skill runtime, and durable worker.

Conceptually:

1. Resolve the approver from a live server-side authentication session.
2. Build the approval request from trusted durable task state and server-side
   policy context. Do not accept a browser/model-supplied `PolicyRequest` as
   proof that an operation belongs to a task.
3. Configure the authenticated approval service with
   `deliver_credential=broker.deliver_credential`.
4. Configure the guarded skill runtime with the same policy engine, trusted
   request factory, and capability gateway.
5. Configure the durable worker with
   `approval_token_resolver=broker.resolver()` and the exact worker identity
   assigned by trusted process orchestration.
6. Keep worker identity, database paths, KMS configuration, and policy rules
   out of task payloads and model-controlled arguments.

The worker identity must come from trusted deployment configuration or
orchestration. A user, model, skill, or task payload must not be able to choose
it. A gateway-configured durable worker must refuse to start without a trusted
resolver bound to its own worker identity.

## Storage and deployment constraints

- Store gateway and broker databases in a private, persistent application data
  directory. Back up both consistently and protect backups as credential-bearing
  security data.
- On POSIX, keep database files owner-only and parent directories inaccessible
  to other local users. On Windows, apply equivalent restrictive ACLs.
- Do not put these SQLite databases on an untrusted or network filesystem.
  The current implementation is a single-host persistence design, not a
  multi-host distributed database or consensus protocol.
- Do not run multiple independent application processes with divergent policy,
  worker identity, or approval state. Multi-host workers require a separately
  validated shared transactional store and authenticated worker transport.
- Monitor database availability, KMS availability, approval expiry, failed
  credential delivery, lease renewal, and recovery outcomes without logging
  credentials or raw sensitive arguments.
- Test restore and key-rotation procedures before accepting production tasks.

## Required release gates

Production privileged execution remains disabled until all are demonstrated:

1. A real KMS-backed AEAD adapter is implemented and reviewed.
2. The authenticated approval service derives requests from trusted task state.
3. The composition root wires gateway, broker, service, runtime, and worker
   without alternate unguarded construction paths.
4. Worker identity is authenticated by the deployment boundary, not merely
   named in configuration.
5. Approval issue, delivery, resolve, consume, revoke, expiry, process restart,
   lease renewal, and crash recovery are tested end to end.
6. Every other execution adapter (terminal, MCP, browser, plugins, workflow,
   specialist provider tools) is inventoried and independently guarded.
7. Independent security review and exact-head cross-platform CI pass.

This approval mechanism is an authorization control. It is not an OS/container
sandbox and does not isolate untrusted skill code from the worker process.
