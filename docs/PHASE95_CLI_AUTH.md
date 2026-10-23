# Phase 95 — Local CLI authentication boundary

## Implemented in this branch

- `core/cli_auth.py` provides first-run admin bootstrap and login over `BuiltInAuthService`.
- First run requires an operator-supplied admin username/password and confirmation; there are no shipped credentials.
- Subsequent runs authenticate an existing account. Password entry uses `getpass` by default, invalid-login responses are generic, and attempts are bounded.
- `yuki.py` now authenticates before constructing the provider/runtime or loading personal state. If login is cancelled or fails, the command loop does not start.
- The raw session token is held in process memory and is never printed; `main` revokes it in a `finally` block on normal exit and recoverable/unexpected command-loop exit.
- `/logout`, `/exit`, and `/quit` terminate the current session.
- `tests/test_cli_auth.py` covers helper bootstrap/login behavior. `tests/test_yuki_cli_auth.py` covers entrypoint denial and session revocation on exit/error.

## Validation status

The code and tests have been committed to the PR branch. They have **not been executed in this environment**. The current-head commit did not return any workflow runs or combined status checks through the available GitHub status query, so CI is not yet verified. Keep the PR draft until a fresh CI run passes and the patch receives review.

## Operational notes and remaining security work

- The auth SQLite database, its parent directory, WAL files, and backups contain sensitive password hashes and session metadata. Restrict filesystem permissions to the OS user running Yuki; verify this on supported operating systems before production use.
- Use a private local machine account and full-disk encryption. This helper is not a defense against malware or another process running as the same OS user.
- The CLI authentication boundary protects the normal `python yuki.py` entrypoint; it does not replace authorization checks at every separately exposed API/service boundary.
- Run `python -m unittest discover -s tests -v` and review all current-head GitHub Actions results before merging.
