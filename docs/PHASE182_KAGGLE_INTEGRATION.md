# Phase 182 — Kaggle account and compute integration

## Connect your Kaggle account

Yuki does not have access to your Kaggle account yet. Connect it on the
machine where Yuki will run; never paste the token into GitHub, source files,
issues, or this chat.

1. Sign in at https://www.kaggle.com/settings/api
2. Generate a Kaggle API token (or use the official CLI OAuth login).
3. Install the official CLI: `python -m pip install kaggle` (current CLI
   documentation requires Python 3.11+).
4. Preferred: run `kaggle auth login` and complete the browser flow.
   Alternatively set `KAGGLE_API_TOKEN`, or place the access token in
   `~/.kaggle/access_token`. Legacy `kaggle.json` is also supported.
5. On Linux/macOS, keep credential files private (directory 0700, file 0600).
6. Verify manually with `kaggle --version` and `kaggle kernels list`.

Official authentication documentation:
https://www.kaggle.com/docs/api
https://github.com/Kaggle/kaggle-cli/blob/main/docs/README.md

## What Yuki adds

- Detect supported local authentication without returning credential values.
- Reject credential files that are symlinks or overly permissive on POSIX.
- Prepare an explicit Kaggle Kernel push command.
- Require an explicit approval flag before a remote kernel submission.
- Allow an accelerator to be selected from a conservative known set.
- Keep credentials out of job metadata and source control.

This is a planning/preflight adapter, not a remote Kaggle API client. It does
not execute a job, install the CLI, or verify your account until run on your
machine with your Kaggle authentication configured.

## First Yuki workload

Create a private Kaggle notebook for:
- validating dataset manifests and license metadata,
- profiling small LoRA/QLoRA training jobs,
- running short evaluation and benchmark batches,
- saving model artifacts with SHA-256 manifests.

Use public or appropriately licensed datasets only. Do not upload private
Yuki memory, user conversations, credentials, proprietary data or screen
captures. Check Kaggle accelerator availability, quotas, internet access,
session limits and current terms before every run. No paid service should be
used without explicit budget approval.

## Limits

Kaggle's published notebook docs describe time-limited sessions and GPU
hardware that may be unavailable or quota-limited. Hardware allocation is
not a permanent worker or guaranteed GPU reservation. Use checkpointing and
make local CPU execution the fallback.
