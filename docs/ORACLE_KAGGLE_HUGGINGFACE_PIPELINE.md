# Yuki Oracle Cloud, Kaggle, and Hugging Face pilot architecture

Status: **architecture and delivery plan only**. No Oracle Cloud tenancy, compute instance, Kaggle account/notebook, Hugging Face repository, credentials, or deployment has been provisioned by this change.

## Goal

Run Yuki as a private, continuously available pilot on Oracle Cloud Infrastructure (OCI), while using Kaggle for bounded training/experimentation, Hugging Face Hub for reviewed datasets and optional model artifacts, and GitHub as the source of truth for code, manifests, evaluations, and deployment automation.

The runtime and training plane are deliberately separate. A training notebook must never receive production credentials, connect to the live Yuki database, or deploy directly to the running service.

## System boundaries

| Plane | System | Responsibilities | Must not do |
|---|---|---|---|
| Runtime | OCI Compute | Run Yuki API/worker in a private network; persist operational state; expose only approved private access | Run unreviewed Kaggle code or auto-install candidate models |
| Training | Kaggle notebooks/jobs | Reproducible experiments, fine-tuning where licensing and hardware permit, evaluation data preparation | Access production secrets, live user data, or OCI production deployment credentials |
| Dataset/model supply | Hugging Face Hub | Versioned dataset/model repositories, model cards, revisions, licenses and provenance | Automatically grant trust based on popularity or a repository tag |
| Engineering/control | GitHub | Source, workflow definitions, pinned manifests, tests, evaluation reports, release approvals and deployment history | Store credentials, private user datasets, or large binary artifacts in Git history |

## Data and artifact flow

1. **Dataset intake:** select a Hugging Face dataset repository and immutable commit revision. Record repository ID, revision, license evidence, source URL, retrieval date, split names, expected schema, content hashes, and intended use in a reviewed manifest under version control.
2. **Dataset review:** check license/terms, provenance, PII/secrets, harmful or poisoned content, duplication, data quality, and train/validation/test leakage. Reject unclear licensing or unreviewed sensitive data. Keep private or sensitive datasets out of public Hub repositories.
3. **Training input:** Kaggle obtains only approved, pinned data. Prefer public datasets or a specifically approved private dataset credential scoped to read-only access. Never upload production conversation logs. Any future user-feedback dataset requires explicit opt-in, minimization, redaction, retention rules, and a documented lawful basis.
4. **Experiment:** notebook code and configuration are versioned in GitHub. Pin Python/package versions, base model revision, random seed, hyperparameters, dataset revision, and training code commit. Record Kaggle accelerator/runtime details and actual resource limits; GPU availability is not guaranteed.
5. **Candidate output:** save model weights and tokenizer/config artifacts to an approved private artifact location (private Hugging Face repository or OCI Object Storage). GitHub stores the small manifest, hashes, evaluation report, and provenance—not model binaries.
6. **Evaluation:** run deterministic unit/contract tests and task-specific model evaluations in a clean environment. Include regression, safety, prompt-injection/tool-use, latency, memory, and license/provenance checks. Compare against the currently approved baseline.
7. **Promotion:** a human reviewer approves a specific immutable artifact digest and report. Only then may a separate GitHub release workflow copy/promote it to the OCI staging artifact store. Deployment uses a versioned release manifest; no notebook deploys to OCI.
8. **Rollback:** retain the last known-good model and application release. Promote by immutable version/digest, observe canary metrics, and roll back automatically or manually on failed health/evaluation thresholds.

## OCI pilot topology

- Start with one supported Linux OCI Compute instance in a dedicated VCN and private subnet. Select shape and storage only after measuring Yuki's CPU/RAM/disk needs and model-serving approach; do not assume the training GPU is also the inference host.
- Keep the application listener on loopback or private interfaces. Use OCI Bastion or a private VPN/SSH tunnel for administrator access. Do not expose the developer preview directly to the public internet.
- Apply least-privilege OCI IAM policies. Prefer instance principals/dynamic groups for workload access to OCI services; do not embed OCI user API keys in the repository or image.
- Store persistent application data on a dedicated encrypted volume with restrictive OS permissions. Keep database, metrics, debug, and worker control interfaces private.
- Put secrets in OCI Vault or an equivalent managed secret store. Rotate credentials and keep them out of cloud-init output, shell history, logs, notebook output, and GitHub artifacts.
- Configure firewall/security lists with default-deny ingress, SSH restricted to the chosen admin path, outbound egress allow-listing where practical, OS security updates, resource limits, log rotation, monitoring, backup, restore, and rollback.
- The first pilot is single-instance and private. High availability, public ingress, multi-tenancy, and production claims are explicitly out of scope until their security and operational gates pass.

**Important Yuki-specific blocker:** the stock web launcher intentionally fails closed in strict/public mode unless deployment-owned production identity, policy, PostgreSQL security runtime, durable audit writer, and independently administered audit checkpoint anchor are injected. Do not bypass this by changing environment flags or binding the developer preview publicly. A private developer pilot and a production web deployment are different release gates.

## GitHub automation design

Keep workflows small, permission-scoped, and independently reviewable:

1. **dataset-manifest-check.yml** — validate schema, immutable revisions, required license/provenance fields, hashes, and prohibited sensitive-data declarations. Do not fetch or execute dataset code in pull-request jobs.
2. **training-config-check.yml** — lint notebook/config metadata, pin dependencies and base model revisions, and verify the training code commit is known. Pull requests must not receive deployment secrets.
3. **kaggle-training.yml** — manually dispatched or explicitly approved scheduled training; use Kaggle secrets only for the Kaggle job. Save logs/reports without secrets and publish candidate artifacts to the approved private artifact destination. Do not grant production OCI credentials.
4. **model-evaluation.yml** — download an exact candidate digest in an isolated runner, run deterministic and task-specific evaluations, and publish a signed/immutable report. A failed or missing report blocks promotion.
5. **candidate-review.yml** — require human approval for model/license/security review, dataset revision, evaluation result, artifact digest, and rollback target.
6. **oci-staging-deploy.yml** — manual approval through a protected GitHub Environment; use short-lived OIDC/workload identity to obtain narrowly scoped OCI access where supported. Deploy only an approved immutable artifact and release manifest.
7. **post-deploy-smoke.yml** — private health/readiness checks, model identity verification, bounded synthetic prompts, latency/resource checks, and rollback signal evaluation.

Use least-privilege workflow permissions, pinned third-party actions, concurrency controls, protected branches/environments, artifact retention limits, and explicit approval for deployments. Avoid long-lived cloud keys in GitHub secrets when workload identity federation is available. Do not put secrets into workflow outputs or artifacts.

## Continuous improvement without uncontrolled self-modification

Yuki may collect operational metrics, user-approved feedback, and defect reports to propose improvements. It must not silently train on private conversations, modify its own production code, change its active model, or deploy itself.

The safe loop is:

- observe → redact/minimize → obtain required consent → create a versioned issue or dataset candidate;
- propose code/model change → open a GitHub PR with bounded diff and provenance;
- run tests and model evaluations in isolated CI;
- human review and approval → stage candidate;
- verify health and user-visible behavior → explicitly promote or roll back.

The existing self-upgrade proposal boundary remains proposal-only. A successful training job, notebook run, benchmark score, or generated patch is not approval.

## Owner-approved pilot defaults

- OCI region: **undecided**. Keep infrastructure configuration region-parameterized; do not create region-bound resources until the owner confirms service availability, residency, latency, and cost.
- Inference: **hybrid**. Begin with the existing hosted-provider path; treat local inference as a fallback candidate that must pass the same evaluation and operational health checks before routing can use it.
- Budget: **flexible**. Still require a monthly estimate, budget alert, and owner approval before provisioning; flexible is not unlimited.
- First objective: **all three evaluation tracks**—assistant response quality, coding/engineering, and agent/tool-use reliability. Establish a baseline for each before fine-tuning; do not combine the scores into one opaque metric.

## Independent evaluation baselines

`evaluation.builtin_benchmarks.build_training_baseline_benchmarks()` returns three independently versioned, content-addressed suites: assistant quality, coding/engineering, and agent/tool reliability. Each suite can be registered and resolved independently through `BenchmarkRegistry`; candidate qualification must retain per-track reports and must not average them into one opaque pass rate.

These initial suites are deterministic keyword smoke checks. They establish reproducible contract baselines, not semantic quality, code correctness, tool execution safety, or production readiness. Expand them with reviewed golden datasets, adversarial cases, calibrated rubrics, and isolated tool simulators before model promotion. The existing `.github/workflows/tests.yml` quality-gates workflow runs `python -m unittest discover -s tests -v` on pull requests, pushes, and manual dispatch; this includes both `tests/test_builtin_benchmarks.py` and `tests/test_training_manifests.py`. Keep these tests in that shared suite unless a measured CI-runtime problem justifies splitting them.

## Manifest validation

- `evaluation/training_manifests.py` validates dataset and model-candidate manifest structure, immutable revisions/digests, timestamp ordering, safe manifest paths, URI schemes, and fail-closed review requirements.
- `tests/test_training_manifests.py` covers valid manifests and rejection cases. The validator is local and side-effect-free: it does not fetch remote files, authenticate reviewer identities, verify remote artifact bytes, execute training code, or promote models. A separate trusted workflow must verify those claims.

## Continuous delivery and synchronization

- [Continuous delivery runbook](CONTINUOUS_DELIVERY.md) explains the one-time VPS bootstrap and automatic, approved release updates, plus Kaggle notebook publishing/training and Hugging Face artifact publishing. GitHub remains canonical; no platform is updated from arbitrary commits.

## Machine-readable contracts and provisioning notes

- [Dataset manifest JSON Schema](../schemas/training-dataset-manifest.schema.json) requires an immutable Hugging Face commit, license review, content digest, intended use, and privacy review.
- [Model candidate JSON Schema](../schemas/model-candidate-manifest.schema.json) binds the base model revision, training code commit, dataset manifests, artifact digest, training configuration, evaluation report, independent reviews, and rollback candidate.
- [OCI pilot provisioning checklist](../deploy/oci-pilot/README.md) defines the required operator inputs, private-network-first sequence, hard stops, and hybrid inference default.
- These schemas are contracts, not proof that an artifact is trustworthy. The current validators and unit tests provide structural checks only; review fields must be checked by a trusted approval process, not accepted as self-attested truth from a Kaggle notebook.

## Initial implementation milestones

### Milestone 1 — repository contracts
- [x] Add a dataset manifest schema with immutable revision, license evidence, content digest, intended use, and privacy review.
- [x] Add a model candidate manifest schema binding base model, dataset revisions, training commit, artifact digest, training metadata, evaluation report, reviews, and rollback reference.
- [x] Add side-effect-free manifest validators and rejection-focused unit tests; remote artifacts and third-party code are not fetched or executed in CI.
- [x] Add independent assistant-quality, coding/engineering, and agent/tool-reliability smoke baselines on the existing immutable benchmark registry.
- [x] Include the new tests in the existing cross-platform unittest quality-gates workflow.
- [ ] Expand smoke checks into reviewed semantic, adversarial, and isolated tool-simulator suites.

### Milestone 2 — Kaggle experiment
- [ ] Choose one narrowly scoped task and baseline model based on current hardware/cost constraints.
- [ ] Create a reproducible notebook with pinned inputs, deterministic seed where supported, bounded runtime, and no production credentials.
- [ ] Export metrics and artifact hashes; compare against the baseline and document limits.
- [ ] Keep this as an experiment until a reviewer accepts the dataset, model license, and evaluation.

### Milestone 3 — OCI private staging
- [ ] Confirm OCI region, tenancy access, supported Linux image, compute shape, budget, and administrator access path.
- [ ] Provision VCN/subnet/security rules, compute, encrypted storage, IAM, Vault, monitoring, and backups through reviewed infrastructure-as-code.
- [ ] Build the deployment-owned production security bootstrap and migration/release gates before any public exposure.
- [ ] Deploy a pinned Yuki application build privately, verify backup/restore, crash/reboot recovery, logs, metrics, and rollback.

### Milestone 4 — guarded model promotion
- [ ] Add protected GitHub Environments and least-privilege OIDC federation to OCI.
- [ ] Add candidate evaluation and human approval gates.
- [ ] Deploy one approved model candidate to private staging, verify model digest and smoke tests, then retain rollback.
- [ ] Only consider automatic promotion after repeated evidence and a separately approved policy; initial pilot promotion remains manual.

## Decisions still required

The architecture can be prepared without secrets, but provisioning and training require these owner decisions:
- OCI region and tenancy/compartment, instance budget, and whether an existing tenancy is available.
- Initial Yuki inference mode: external hosted provider, CPU local model, or GPU-backed inference. Kaggle GPU training does not imply OCI GPU inference.
- First training objective (for example, domain-specific response quality, classification, embeddings, or tool-use evaluation) and acceptable model/license constraints.
- Whether Hugging Face artifacts/datasets are public or private; default to public datasets only after license review and private model artifacts until approved.
- Whether the first pilot is single-user only; default to one owner and synthetic/test data.

## Exit criteria

The pilot is ready for controlled use only when the OCI instance is private and recoverable, the exact Yuki commit and model digest are recorded, secrets are outside source control, backup/restore and rollback have been rehearsed, the model passes its approved evaluation suite, and the deployment security gates are satisfied. A Kaggle notebook completing successfully is not proof of production readiness.
