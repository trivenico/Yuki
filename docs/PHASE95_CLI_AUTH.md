# Phase 95 — Local CLI authentication boundary

## Added

- `core/cli_auth.py` provides a reusable local login/bootstrap flow over `BuiltInAuthService`.
- First run creates an operator-supplied admin username/password, with confirmation; there are no shipped credentials.
- Subsequent runs authenticate an existing account. Password entry uses `getpass` by default, invalid credentials are generic, and attempts are bounded.
- The helper never prints or persists the issued session token.

## Critical integration status

This phase adds the authentication boundary and unit tests, but **does not yet gate `yuki.py` command dispatch**. It is not sufficient by itself to claim that the terminal application is authenticated. The next integration must require a non-null session before constructing the command loop, keep the session in process memory, and revoke it in a `finally` block on exit. Protected commands must not be callable through an alternate unauthenticated entry point.

## Operational notes

- The auth SQLite database, its parent directory, WAL files, and backups contain sensitive account/session metadata. Restrict filesystem permissions to the OS user running Yuki.
- Use a private local machine account and full-disk encryption. This helper is not a defense against malware or another process running as the same OS user.
- Run `python -m unittest discover -s tests -p 'test_cli_auth.py' -v` and the existing authentication suite in CI.
