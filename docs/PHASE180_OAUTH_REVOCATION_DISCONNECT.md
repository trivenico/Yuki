# Phase 180 — OAuth revocation and account disconnect

## Delivered

- Add a dedicated pinned HTTPS form transport operation for OAuth revocation endpoints that may return an empty successful HTTP response.
- Keep the token-exchange and refresh JSON response contracts strict and unchanged.
- Add an owner-invoked disconnect coordinator that looks up the owner/provider/account credential.
- Prefer refresh-token revocation, falling back to access-token revocation when no refresh token exists.
- Make exactly one remote revocation attempt; never retry an ambiguous result.
- Always attempt local encrypted-vault deletion even if remote revocation fails or times out.
- Return metadata only; never return token values or provider response bodies.

## Operational semantics

A successful local disconnect means Yuki has deleted its local credential. It does not prove the provider invalidated the token. The response reports remote revocation separately. If remote revocation fails, the user may need to revoke the app in the provider's account-security settings.

The host must configure the exact provider revocation endpoint in the pinned transport allow-list. No web route or UI action is added in this phase; this is a core service boundary.
