# Yuki background evolution reviews

Yuki's background evolution is an opt-in local review loop, not an autonomous self-modifying updater.

## Enable it

Open the desktop app, choose **Settings**, then under **Self-Evolution** enable **Enable local background evolution reviews (proposals only)** and save. Restart Yuki for the setting to take effect. The equivalent configuration key is `background_evolution_enabled: true`; `YUKI_BACKGROUND_EVOLUTION_ENABLED` can override it.

When enabled, the shared runtime checks after 03:00 local time and runs at most one review cycle per local calendar day. It analyzes only observations explicitly recorded in Yuki's local evolution log. It does not record microphone input, watch arbitrary files, call a model or network service, edit code/configuration, install packages, or execute actions.

## Review the results

- `/evolution status` shows the local observation/report status.
- `/evolution auto status` shows whether background reviews are enabled.
- `/evolution record failure | concise summary` records a failure for later review (categories: task, feedback, lesson, failure, preference).
- `/evolution run` asks for approval before generating a local proposal report now.
- `/evolution review PROPOSAL_ID accept` records that a proposal is accepted for manual follow-up; it does not apply the proposal.
- `/evolution review PROPOSAL_ID reject` rejects a proposal.

Reports are stored in the configured workspace as `yuki_evolution_report.json`. Observations and state are stored in `yuki_evolution_observations.json` and `yuki_evolution_state.json`.

## Self-upgrade safety boundary

This feature is not a code-writing or application-updating engine. It is an observation and proposal stage only. A future self-upgrade pipeline must prepare changes in an isolated branch/worktree, run automated tests and security checks, show a reviewable diff, require explicit approval before modifying the active installation, preserve a known-good rollback, and verify health after restart. Failed validation must leave the current installation unchanged. Background proposal generation must never be interpreted as permission to apply a change.
