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
