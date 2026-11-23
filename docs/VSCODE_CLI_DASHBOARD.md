# Run Yuki CLI and Dashboard in VS Code

The repository includes a VS Code task that starts the terminal assistant and
the Textual supervision dashboard in separate integrated terminals.

## Start both

1. Open the Yuki repository folder in VS Code.
2. Install the base dependencies and optional dashboard dependency:

   ```bash
   python -m pip install -e .
   python -m pip install -r requirements-ui.txt
   ```

3. Open **Terminal → Run Task...**.
4. Select **Yuki: Start CLI + Dashboard**.

VS Code opens two dedicated terminal panels:
- **Yuki CLI** runs `python yuki.py` for normal terminal interaction.
- **Yuki Dashboard** runs `python -m scripts.run_dashboard` for the supervision UI.

The dashboard is a separate process and terminal, so it does not take over the
CLI input. It displays a read-only diagnostics report on startup. Enter
`/doctor` or `/diagnostics` to refresh it. The report includes Python/platform
details and capability availability without printing environment variables,
credentials, prompts, or task payloads. CLI and dashboard lifecycle failures are
recorded in the local `.yuki/diagnostics.json` file (up to 100 events); only the
timestamp, component, event name, and exception class are stored. It does not
install packages, start services, change configuration, or send model requests.

Stop each process with its own terminal's Ctrl+C or the dashboard's `/quit`
command. Source files remain editable in the VS Code editor while both are
running.

## If startup fails

- If the CLI reports a Python import error, verify that the VS Code interpreter
  is the environment where Yuki was installed.
- If the dashboard reports that Textual is missing, install
  `requirements-ui.txt` in that same environment.
- The dashboard is optional: a dashboard startup failure does not prevent
  starting Yuki CLI separately.
- Use the VS Code Explorer and editor to inspect and repair source files. The
  dashboard is a supervision console, not a source-code editor or automatic
  error fixer.


## Operational awareness integration

The dashboard also accepts an injected, authenticated
`OperationalAwarenessService`. When the host application composition supplies
that service and the authenticated actor ID, `/awareness` renders a bounded
task/worker snapshot and attention suggestions. It is deliberately unavailable
in the standalone developer launcher by default: that launcher has no
authenticated account session or worker-health authorization context, and must
not invent one or expose global worker records. Production integrations must
supply the service, an owner-scoped TaskRuntime, and an explicit worker-health
authorization callback.


## Proactive alert and notification controls

The dashboard accepts three additional optional dependencies from an
authenticated host composition: an owner-scoped `ProactiveAlertStore`, a
`ProactiveNotificationPreferences` instance, and a
`ProactiveNotificationDispatcher` whose transport and preference resolver
are configured by the host. The standalone VS Code launcher intentionally
does not invent an account identity or enable a notification transport.

When those dependencies and the authenticated actor ID are injected:

- `/alerts` displays only that actor's recent alert history.
- `/notifications` displays the actor's preference and recent delivery history.
- `/notifications on` or `/notifications off` explicitly changes that
  actor's persisted notification preference.
- `/notify dispatch CONFIRM` explicitly dispatches at most one eligible
  pending notification through the injected transport. A disabled preference
  prevents dispatch.

No notification is sent merely by opening the dashboard. The dispatcher
rechecks the preference before transport invocation, and delivery outcomes
are stored separately from task state. The host must provide a real
transport, stable notification-ID idempotency where supported, and an
explicit scheduler if recurring delivery is desired.
