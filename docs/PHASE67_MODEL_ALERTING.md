# Phase 67 — Persistent Hugging Face baseline and review alerts

This phase wires the Phase 65 scout and Phase 66 snapshot comparator into one scheduled workflow. After it is merged into the repository's default branch and GitHub Actions is enabled, it runs every Monday at 09:17 UTC or on manual dispatch.

## Workflow behavior

1. Run the offline unit tests for the scout and snapshot comparator.
2. Query public Hugging Face model metadata using the bounded Phase 65 scout.
3. If the dedicated `yuki-model-scout-baseline` branch exists, compare the new snapshot against its `baseline.json`.
4. When new model IDs or changed metadata are detected, create or comment on an open issue titled `[Yuki Model Review] Candidate updates detected`.
5. Update the dedicated baseline branch and upload reports as an artifact retained for 30 days.

The first successful run initializes the baseline and intentionally emits no alert. A later run can compare against it. A failed scout, comparison or alert step stops the workflow before baseline update, so a failed run should not replace the prior snapshot.

## Permissions and data

The workflow requests `contents: write` to update the dedicated baseline branch and `issues: write` to create/comment on review issues. Repository Actions settings must permit the workflow token to write contents and issues. If write access is disabled by organization policy, the job fails rather than silently claiming alerts were delivered.

The baseline contains only bounded public model metadata from Hugging Face, not model weights, prompts, completions, user data or credentials. It is stored on a dedicated branch so it does not add generated reports to the application source tree. Review branch protection and retention expectations before enabling this in a sensitive repository.

## Alert interpretation

- New IDs are candidate leads, not proof of quality or a release announcement.
- Changed license metadata means a human must re-check the official model card and license before use.
- Task/tag/timestamp changes are triage signals only.
- An ID missing from the current bounded snapshot does not prove deletion.
- A notification does not authorize downloading, executing, evaluating against private data, or activating a model.

No model weights are downloaded; no remote code is run; no inference provider is called; no candidate is automatically installed or activated.

## Operational limitations

- GitHub scheduled jobs may be delayed and run only after this workflow is merged into the default branch. Use manual dispatch for an on-demand run.
- If the baseline branch is deleted, the next successful run initializes it without an alert.
- This first version sends alerts through GitHub Issues, not email, Slack or push notifications.
- Reports are artifacts with 30-day retention; download reports needed for longer-term audit.
- The workflow is a candidate-change signal, not an exhaustive feed. Discovery remains bounded by the task allow-list and per-task result cap.
