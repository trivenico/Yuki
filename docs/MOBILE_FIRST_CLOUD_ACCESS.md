# Optional Android and mobile access

## Product decision

Yuki's primary daily-use experience is the native desktop application on Windows, macOS, and Linux. The owner expects to use Yuki from a desktop PC or laptop, so Android and mobile-browser access are **secondary enhancements**, not a prerequisite for completing or launching Yuki.

Do not divert core engineering effort into a mobile application, public API, PWA, or cloud control plane while the desktop runtime and first usable model path remain unfinished.

This document preserves a future architecture direction only. It does not claim that a mobile app, public API, or cloud deployment exists.

## Future target architecture

```text
Android browser / optional PWA
          |
      HTTPS only
          |
  Owner-authenticated API
          |
  Shared application-service boundary
          |
  Yuki runtime (local desktop or private cloud)
```

The mobile UI must reuse the same application-service contracts as desktop. Do not put provider routing, memory policy, approval rules, or task execution inside the browser.

## When mobile work should begin

Start mobile access only after these desktop milestones are met:

1. The canonical conversation runtime is integrated into the desktop application and passes the supported cross-platform CI matrix.
2. The owner can sign in, chat with a configured real model, reopen durable conversation history, and sign out safely.
3. Memory inspection, consent, backup, restore, and recovery behavior are documented and tested.
4. Desktop packaging has been smoke-tested on clean Windows, macOS, and Linux environments.
5. The API boundary has a security design review before any network listener is exposed.

The first mobile slice should be owner-only sign-in, chat/history, provider status, and read-only task status. Sensitive approvals and destructive actions should wait until their exact scope and consequences can be reviewed safely on a small screen.

## Security requirements

- Never expose SQLite files, the desktop profile directory, OS keyring, shell, Docker socket, or raw provider credentials to a browser or public network.
- Use owner-only authentication, short-lived server-side sessions, secure HttpOnly/SameSite cookies, CSRF protection for cookie-authenticated mutations, rate limits, login throttling, and explicit logout/revocation.
- Require TLS, strict origin/CORS allowlisting, request-size limits, schema validation, security headers, and redacted audit events.
- Keep model credentials server-side. The browser must never receive provider API keys.
- Enforce authorization and approval in the application service on every request; hiding a button is not authorization.
- Keep chat/read-only access separate from tool execution. Require fresh, scoped approval for sensitive actions and re-check it at execution time.
- Keep the first hosted version single-owner and single-tenant. Avoid SaaS complexity for a personal assistant.
- Do not make Google OAuth mandatory. Google, GitHub, LinkedIn (where supported), and other account integrations remain optional, individually authorized connectors.

## Cloud deployment constraints

Oracle Cloud may later provide an always-on private runtime, but deployment is not complete until firewall/egress policy, service supervision, secret storage, encrypted backups, monitoring, patching, and restore drills are verified. Do not treat Docker alone as a security boundary.

Keep local desktop/portable storage distinct from cloud storage. Cross-device synchronization must use an explicit authenticated protocol, not shared filesystem paths.

## Training

Training is a parallel engineering track after the core desktop conversation and provider path is stable. Use Kaggle GPU notebooks or another managed GPU environment for experiments; a phone is not a suitable local training environment. Keep datasets, checkpoints, and secrets under controlled storage. Do not put cloud API keys or private datasets in public notebooks or repository commits.

The first training milestone should be a reproducible small fine-tuning experiment on a suitable open model, not training a foundation model from scratch. Require dataset license review, held-out evaluation, checkpoint provenance, and rollback before any model is offered to the live assistant.

## Acceptance criteria for future mobile access

- Owner can sign in from an Android browser without weakening desktop authentication.
- Chat history remains owner-isolated and survives server restart.
- Revoked sessions cannot read history or submit requests.
- Browser traffic contains no provider API keys or desktop credentials.
- Unapproved tools cannot execute through direct API requests.
- Encrypted backup can be restored and service restarted without losing conversations.
