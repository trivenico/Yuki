# Yuki quick start and honest readiness status

## Current status

Yuki is an active development project, not yet a production-ready personal assistant. Do not connect primary email, financial accounts, production credentials, or privileged device controls until release gates pass.

## Minimal local setup

1. Use a fresh virtual environment with a supported Python version.
2. Install dependencies using the repository's current requirements file.
3. Copy the example configuration to a local config file and inspect all enabled skills.
4. Keep provider mode on `mock` for the first startup and tests. Do not put API keys in YAML or commit them.
5. Run `python -m unittest discover -s tests -v`.
6. Start the application using the command documented by the current entrypoint and check its logs and health/readiness report.

Before real model use, explicitly configure the selected local provider and confirm that the model is installed and responding. Cloud routing must be opt-in and restricted to data the user has approved for external processing.

## Safe initial use

- Start with research, drafting, planning, local notes, and read-only tasks.
- Review sources and generated content before relying on them.
- Require human confirmation before sending messages, publishing content, spending money, deleting files, changing security settings, or controlling devices.
- Keep backups of the workspace and test restore before storing important personal memory.
- Keep plugin discovery disabled unless every enabled module is reviewed and explicitly trusted.
- Do not run untrusted skills with unrestricted host privileges.

## Production readiness gate

Do not call Yuki production-ready until the full CI suite passes on the PR head, all imports and integration paths are present, dependency and secret scans have been reviewed, risky tool execution is isolated, privacy/retention/export/deletion behavior is tested, recovery and backups are verified, and monitoring/rollback are documented.

## Known blocker being addressed

The current main-branch CI run reported 174 tests with 7 errors and 1 failure. The visible causes include an incomplete approval-flow test dependency set, a registry constructor mismatch for DeviceScannerSkill, and provider validation occurring after local provider construction. This branch fixes the latter two. The approval-flow dependency issue must be implemented and tested or resolved through a reviewed test/design change before release.
