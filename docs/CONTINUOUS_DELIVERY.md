# Yuki continuous delivery: GitHub to OCI, Kaggle, and Hugging Face

Status: **implementation plan; no credentials, cloud resources, notebooks, Hub repositories, or deployments have been configured**.

## Core rule

GitHub is the source of truth. A merge to the protected default branch creates a tested source revision; a separately approved release creates a deployable immutable version. Do not deploy every push, and do not let a running VPS execute arbitrary branch code.

The initial OCI instance is cloned/installed once. Subsequent releases update that installation in place from a pinned release artifact. Re-cloning the repository for every change is unnecessary.

## 1. OCI Linux VPS: one-time bootstrap, then controlled updates

### One-time bootstrap
1. Provision the private OCI instance, OS account, encrypted persistent data volume, network controls, Vault access, monitoring, backups, and private administrator path.
2. Install the supported Python/runtime dependencies and a dedicated non-root Yuki service account.
3. Install Yuki from an exact Git commit or signed release into a versioned directory, for example `/opt/yuki/releases/<release-id>`.
4. Keep mutable state outside the code directory (for example `/var/lib/yuki`), and keep secrets in OCI Vault or a root-readable runtime secret mount. Never put either in the Git checkout.
5. Create a stable `/opt/yuki/current` pointer and a systemd service. Record the deployed commit, release ID, model ID, and artifact digest in deployment metadata.

### Every approved application release
1. A protected GitHub Actions release workflow runs only after merge, required CI, and a protected GitHub Environment approval.
2. `.github/workflows/linux-cli-release.yml` builds a Python wheel and source distribution on a version tag (or manual dispatch), installs the wheel into an isolated virtual environment, checks `yuki --help`, and emits `release-manifest.json` plus `SHA256SUMS.txt`. Tagged builds receive GitHub build-provenance attestations. This is a CLI distribution artifact, not a complete OCI deployment image or proof that the production web runtime is configured. The deployment references the exact commit and artifact digest, never a floating `latest` tag.
3. A private, ephemeral GitHub Actions runner inside the OCI private network (or a separately reviewed private deployment agent) receives only the narrowly scoped deployment permission. It must accept deployment jobs only from this repository and protected release workflow; never run untrusted pull-request jobs on it.
4. The deployment stages the new version alongside the current version, verifies checksum/signature, performs preflight and compatible migrations, switches the service to the new version, restarts, and runs private readiness/smoke checks.
5. If checks fail, switch the current pointer back to the last known-good version, restart, and report a failed deployment. Preserve logs and release metadata.
6. Retain a bounded number of known-good releases and rehearse rollback and restore.

**Network constraint:** a GitHub-hosted runner cannot reach a private OCI subnet by default. Do not open SSH or the application port to the public internet to make deployment convenient. Use a private ephemeral runner or a reviewed pull-based agent. Runner provisioning and trust boundaries must be completed before enabling deployment.

**Trigger policy:** ordinary pushes and pull requests run tests only. A merge does not automatically mean production deployment. The initial private pilot uses a manually approved staging release; production promotion remains a separate decision.

## 2. Kaggle: publish pinned experiments, not a permanent live clone

- Keep notebooks, training scripts, lockfiles, configs, and dataset manifests in GitHub.
- Publish a notebook revision to Kaggle through a dedicated workflow using the Kaggle API only after review. Store the Kaggle API credential in a protected GitHub Environment secret; never commit it or print it in logs.
- Pin the notebook's Git commit, base model revision, dataset revision, dependency versions, seed, and configuration digest. Kaggle jobs should use only approved training inputs and bounded runtime/accelerator settings.
- Trigger training manually or through an explicitly approved schedule. Do not trigger expensive GPU jobs for every commit.
- Kaggle receives no OCI production credentials, live database access, deployment token, or private user conversation data.
- Export metrics, logs with secrets removed, and candidate artifacts to an approved destination. The workflow records artifact hashes and a candidate manifest back in GitHub.
- Treat Kaggle's notebook copy as a generated execution revision. GitHub remains canonical; do not make ad-hoc Kaggle edits the only copy of important training logic.

If the Kaggle API or account configuration changes, update the publisher workflow; users should not need to manually re-clone the project.

## 3. Hugging Face: publish immutable datasets and approved model candidates

- GitHub manifests pin exact Hub repository IDs and commit revisions. Runtime and training jobs must resolve those revisions, not a moving branch or tag.
- A protected publishing workflow uses a narrowly scoped Hugging Face token stored as a protected GitHub Environment secret. Prefer repository-scoped write access; training/evaluation jobs use read-only access where possible.
- Dataset publishing occurs only after provenance, license, privacy, content-hash, and human-review checks. Do not upload private user data or production conversations without explicit opt-in and a separately approved data process.
- Model candidates are uploaded to a private or otherwise approved repository with an immutable revision and SHA-256 digest. Candidate manifests include base model revision, training code commit, dataset manifests, training config digest, evaluation report digest, review status, and rollback candidate.
- A Hub upload is not model approval. Yuki runtime must continue using the currently approved model until the candidate passes the three evaluation tracks (assistant quality, coding/engineering, and agent/tool-use reliability), license/security/compatibility gates, and human approval.
- Promote by immutable revision/digest. Keep the previous approved revision available for rollback.

## 4. Recommended GitHub workflow boundaries

| Workflow | Trigger | Credential scope | Output |
|---|---|---|---|
| CI and security checks | Pull request / protected branch push | Read-only repository | Test and security results |
| Release build | Protected branch merge or version tag | Read-only repository | Versioned package, checksum, provenance |
| OCI staging deploy | Manual dispatch after release + protected Environment approval | Private runner; narrowly scoped OCI deployment identity | Pinned staging release and smoke-test report |
| Kaggle notebook publish | Manual dispatch or reviewed training release | Kaggle publisher credential only | Versioned notebook revision |
| Kaggle training | Manual dispatch / approved schedule | Kaggle execution credentials only | Candidate metrics and artifact manifest |
| Hugging Face publish | Approved dataset/model promotion | Scoped Hub token | Immutable Hub revision |
| Model qualification | Candidate manifest submitted | Read-only artifact access | Three-track evaluation and qualification report |

Use workflow-level least-privilege permissions, pinned third-party actions, protected branches/environments, concurrency controls, artifact retention, and secret masking. Never use `pull_request_target` to execute untrusted pull-request code with secrets.

## 5. What will be automatic, and what still needs setup

| Item | Automatic after setup? | One-time or approval requirement |
|---|---|---|
| VPS gets new approved Yuki release | Yes | Provision private runner/agent, service account, deployment workflow, protected environment |
| VPS restarts and verifies health | Yes | Implemented deployment hook and health checks; rollback tested |
| Kaggle notebook updates | Yes | Kaggle account/API credential and publisher workflow |
| Kaggle training run | Only when explicitly triggered/scheduled | GPU availability, approved input manifests, budget/time limits |
| Hugging Face dataset/model upload | Yes, after approval | Hub repos, scoped token, license/privacy and artifact review |
| New model becomes active in Yuki | No, not initially | Evaluation, human approval, staged rollout, explicit promotion |
| New GitHub PR code runs on VPS | No | PRs must never deploy directly |

## 6. Enablement checklist

- [ ] Confirm protected default branch and required CI/review rules.
- [ ] Confirm OCI region, tenancy, compartment, budget alert, and private admin path.
- [ ] Provision private deployment runner/agent with restricted repository and workflow access.
- [ ] Create a protected GitHub Environment for OCI staging with required reviewers.
- [x] Add versioned Linux CLI wheel/source packaging, isolated install smoke check, SHA-256 manifest, and tagged-build provenance attestation workflow.
- [ ] Implement the OCI runtime-specific deployment package/image, staged activation, health checks, and rollback.
- [ ] Create Kaggle notebook target and protected publisher credential.
- [ ] Create Hugging Face dataset/model repositories and least-privilege tokens.
- [ ] Add manifest validators and CI tests; do not trust self-asserted approval fields from notebooks.
- [ ] Exercise the full pipeline with synthetic data and a no-op/staging release before any real user workload.

Until these are complete, the safe process is still manual deployment by an authorized operator. No workflow should assume credentials exist or bypass Yuki's production security bootstrap.


## Linux CLI release artifact workflow

The repository's `.github/workflows/linux-cli-release.yml` is intentionally separate from the native desktop packaging workflow. It builds the installable Python wheel and source archive, verifies the wheel in a clean virtual environment, and emits a small release manifest and checksums. A version tag additionally triggers GitHub build-provenance attestation for the wheel and source archive.

The workflow does not publish to PyPI, deploy to OCI, build or attest a production container, provision runners, or grant cloud credentials. A manually dispatched run creates a retained workflow artifact; a tagged run also attests the distribution files. Before OCI deployment, Yuki still needs a runtime-specific image/package, the production security dependencies and injected deployment-owned runtime, and a reviewed private deployment/rollback mechanism.
