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

The code and tests have been committed to the PR branch. GitHub Actions on commit `78309c5318f3bdc9dc5fffe74e283ea38dd6d65d` completed successfully, including the cross-platform quality gate (Ubuntu, macOS, Windows) and built-in authentication security tests. The quality gate ran 525 tests successfully; auth test jobs also passed. This is CI evidence for that commit, not a complete production security certification.

## Operational notes and remaining security work

- The auth SQLite database, its parent directory, WAL files, and backups contain sensitive password hashes and session metadata. Restrict filesystem permissions to the OS user running Yuki; verify this on supported operating systems before production use.
- Use a private local machine account and full-disk encryption. This helper is not a defense against malware or another process running as the same OS user.
- The CLI authentication boundary protects the normal `python yuki.py` entrypoint; it does not replace authorization checks at every separately exposed API/service boundary.
- Run `python -m unittest discover -s tests -v` and review all current-head GitHub Actions results before merging.
