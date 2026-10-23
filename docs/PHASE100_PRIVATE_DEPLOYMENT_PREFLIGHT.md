# Phase 100 — Private deployment preflight

## Purpose

Provide a fail-closed configuration gate before Yuki is exposed through a private remote-access layer. This is not an HTTP server, tunnel installer, cloud provisioner, or production certification.

Run with:

```bash
python scripts/private_deployment_preflight.py
```

The command intentionally fails unless the operator explicitly configures private deployment mode, a loopback bind host, an absolute auth database path, a private access layer, a protected secret source, verified backup restore, and the owner identity. Public ingress is prohibited. On POSIX, it checks restrictive permissions on an existing auth database or its parent directory. Windows ACL review is explicitly surfaced as a warning because POSIX mode bits do not validate Windows ACLs.

## Intended architecture

- Oracle Cloud: private control plane and durable task coordination, once the HTTP API and service lifecycle are implemented and reviewed.
- Laptop worker: optional outbound-only worker with a dedicated identity and narrow task permissions.
- Remote access: WireGuard or Cloudflare Access/Tunnel; do not publish the raw Yuki application port.
- Initial rollout: owner-only, read-only dashboard, then explicitly approved task actions after authentication, authorization, audit, backup/restore, and cancellation tests pass.

## Required before deployment

1. Implement and test a real HTTP application and service supervisor.
2. Integrate built-in authentication into that HTTP boundary, including authorization checks for every route, CSRF protections for cookie-based browser sessions, and rate limiting.
3. Configure the chosen private access layer and restrict cloud ingress.
4. Protect authentication DB, WAL/SHM files, secrets, and backups; apply platform-specific ACLs.
5. Verify backup restoration, restart behavior, task recovery, and worker disconnect.
6. Complete CI and a security review of authentication PRs and workflow cancellation PRs.
7. Provision infrastructure explicitly and record the actual endpoint and service health only after successful deployment.

This phase only adds preflight checks and tests. It does not claim any cloud resource has been created or that Yuki is remotely reachable.
