# Phase 182 — Kaggle account and compute integration

## Connect your Kaggle account

Yuki does not have access to your Kaggle account until you authenticate on
the machine where Yuki will run. Never paste a Kaggle token into GitHub,
source files, issues, or this chat.

1. Install Python 3.11+ and the official Kaggle CLI:
   `pipx install kaggle` or `uv tool install kaggle`.
2. Run `kaggle auth login` and complete the browser authorization.
   On a headless machine, use `kaggle auth login --no-launch-browser`.
3. Verify with `kaggle --version` and `kaggle kernels list --mine --page 1`.
4. Yuki can verify the authenticated CLI session using a bounded, read-only
   `kaggle kernels list --mine --page 1` request. It captures output privately
   and returns only success/failure; it never returns kernel names or tokens.
5. Alternative non-interactive auth: `KAGGLE_API_TOKEN` or
   `~/.kaggle/access_token`. Legacy `~/.kaggle/kaggle.json` is supported.
   Keep POSIX credential files mode 0600 and the containing directory 0700.

Official references:
- https://www.kaggle.com/docs/api
- https://github.com/Kaggle/kaggle-cli/blob/main/docs/README.md
- https://github.com/Kaggle/kaggle-cli/blob/main/skills/references/auth.md

## What Yuki implements

- Detect supported local token sources without returning credential values.
- Verify OAuth and other active CLI authentication using a bounded read-only
  command, without exposing captured stdout/stderr.
- Reject credential files that are symlinks or overly permissive on POSIX.
- Validate notebook metadata identity against the requested owner/kernel ID.
- Prepare a fixed Kaggle Kernel push argv only after explicit approval.
- Restrict accelerators to a conservative allowlist.
- Keep credentials out of job metadata and source control.

This is a local account preflight and submission planner, not a remote Kaggle
API client. It does not install the CLI, authenticate your account on your
behalf, submit a notebook, or reserve a GPU.

## First Yuki workload

Create a private Kaggle notebook for:
- validating dataset manifests and license metadata,
- profiling small LoRA/QLoRA training jobs,
- running short evaluation and benchmark batches,
- saving model artifacts with SHA-256 manifests.

Use public or appropriately licensed datasets only. Do not upload private
Yuki memory, user conversations, credentials, proprietary data or screen
captures. Check accelerator availability, quotas, internet access, session
limits and current terms before every run. No paid service should be used
without explicit budget approval.

## Limits

Kaggle notebook sessions are time-limited and accelerator allocation is
quota-limited and not guaranteed. Some hardware is restricted or may not be
available to all accounts. Use checkpointing and local CPU execution as the
fallback. A Kaggle notebook is an ephemeral training worker, not a permanent
Yuki host.
