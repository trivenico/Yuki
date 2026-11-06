# Yuki CLI: companion reminders

The CLI reminder commands use the same authenticated ASGI API and owner-scoped SQLite store as the web companion. They do not initialize a model provider and they do not create a separate reminder database.

## Start the local API

```sh
python -m api.server --host 127.0.0.1 --port 8765
```

Provision an account through the existing administrative setup. Sign in through the authenticated Yuki interface or API, then provide the resulting session bearer token to the CLI in the `YUKI_ACCESS_TOKEN` environment variable. Treat this token like a password; do not put it in source control, shell history, or shared logs.

## Commands

```sh
yuki reminders list
yuki reminders list --include-inactive
yuki reminders add --text "Review the deployment" --due-at "2026-11-06T10:30:00+04:00"
yuki reminders cancel REMINDER_ID
```

Use `python yuki_cli.py` instead of `yuki` when running from a source checkout without an installed console entry point.

The `--due-at` value must be a timezone-aware ISO 8601 timestamp. Reminder IDs are the 32-character identifiers returned by the API. The API derives the owner from the authenticated session; the CLI never accepts an owner ID.

## Security and current limitations

- Requests are hard-wired to `http://127.0.0.1:8765`; no arbitrary API URL or remote host can be supplied.
- Authentication is a bearer session token in the process environment. Expired/revoked sessions fail closed.
- Reminder listing, creation and cancellation reuse the authenticated API's owner checks.
- The reminder dispatcher and real notification channel are separate. The default bootstrap does not configure a notification adapter, so these commands manage reminders but do not claim that OS notifications are delivered.
- The CLI does not execute reminder text, tools, shell commands, or agent actions.
