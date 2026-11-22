## 14. Exact-token cleanup after failed desktop sign-in

A desktop sign-in can fail after the OS keyring has accepted the newly issued token—for example, when the immediate read-back fails. The failure path now reacquires the shared credential-slot lock, reads the current value, and deletes it only when it exactly matches the token issued by that failed attempt. A different value is preserved because it may belong to a newer concurrent login. The issued session is then revoked independently of keyring cleanup. If cleanup or revocation cannot be confirmed, the raised startup error explicitly reports that uncertainty rather than claiming complete rollback.

Regression coverage includes a one-shot read-back failure (exact token removed and session revoked), a corrupt/different stored value (preserved), and a cleanup-delete failure (stale credential may remain but the issued session is revoked). Exact-head CI and runtime verification remain pending; no local test run is claimed.
