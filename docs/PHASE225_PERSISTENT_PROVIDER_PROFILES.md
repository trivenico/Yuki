# Phase 225 — Persistent provider profiles and OS credential vault

## Scope

This phase introduces persistent owner-scoped provider profile metadata and a
separate credential-vault boundary. It is the persistence/security foundation
for a unified desktop and CLI provider manager; it does not claim that either
surface is fully integrated yet.

## Security properties

- Profile metadata is stored in SQLite; API keys are never written to the database.
- The default credential adapter uses Python keyring and rejects a backend whose
  priority is non-positive (including fail/in-memory backends).
- Tests inject an in-memory vault only; it is not a production fallback.
- Metadata listing is secret-free. Credential resolution is a separate
  runtime-only method and fails closed when the OS vault has no secret.
- Profiles are owner-scoped, names are validated, and endpoint URLs require
  HTTPS, exact hostname approval, no URL userinfo/query/fragment, and port 443.
- Credential rotation stores the new secret before atomically switching metadata;
  failure to update metadata removes the newly inserted secret on a best-effort basis.
- Deletion removes metadata transactionally, then deletes the vault entry. A vault
  deletion failure can leave an orphan secret; it cannot leave a usable profile.
- POSIX metadata directory/database permissions are restricted to the current user.

## Important deployment caveats

Exact hostname approval is not a complete SSRF defense. Production endpoint
connection code must enforce DNS/IP egress policy at connection time, reject
redirects, and block loopback, link-local, private, multicast, and reserved
destinations unless an explicitly reviewed local-provider policy permits them.
DNS rebinding must be addressed by validating the resolved address used by the
actual socket connection. Never infer protocol compatibility from a provider name.

The OS keyring must be initialized and unlocked in the target user session.
Headless Linux deployments need an explicitly configured Secret Service or
another reviewed vault adapter. If unavailable, profile creation/use must fail
closed; there is no plaintext config fallback.

## Acceptance and remaining integration

Tests cover owner isolation, persistence, secret absence from SQLite/metadata,
rotation, deletion, malformed identifiers/endpoints, missing vault entries,
and closed-store behavior. Follow-up work must integrate this store with
ProviderManager, add CLI add/list/select/remove/test flows using hidden prompt
input (never command-line secret arguments), expose an authenticated owner API,
and build the desktop provider manager against that same API.
