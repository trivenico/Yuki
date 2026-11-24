# Audit 076 — Software Supply Chain, Dependency Integrity & Build Pipeline Security

**Scope:** Python dependency manifests, package installation, Docker image build, GitHub Actions workflows, permissions, artifact SBOM/checksums, and build provenance.

## Reviewed source

- `.github/dependabot.yml`
- `.github/workflows/dependency-audit.yml`
- `.github/workflows/tests.yml`
- `.github/workflows/desktop-packages.yml`
- `.github/workflows/hf-model-scout.yml`
- `.github/workflows/hf-model-change-alerts.yml`
- Additional workflow inventory under `.github/workflows/`
- `pyproject.toml`
- `requirements.txt` and optional `requirements-*.txt` manifests
- `docker/Dockerfile`
- `tools/dependency_audit.py`
- `tests/test_dependency_audit.py`
- `core/trusted_sbom_inventory.py`
- Related release-readiness documentation

## Positive controls observed

- Dependabot weekly update configuration covers pip and GitHub Actions.
- Main test and dependency-audit workflows declare `contents: read`.
- CI runs a dependency consistency check (`pip check`), compile checks and unit tests.
- Dependency manifest audit detects duplicate normalized package names and conflicting declarations across requirements files.
- Desktop packaging emits a CycloneDX environment SBOM and SHA-256 checksum manifest.
- Main-branch desktop artifact workflow creates GitHub build-provenance attestations for checksum manifests.
- Docker image runs as non-root UID 10001 and installs only the base requirements manifest.
- Trusted SBOM verifier authenticates a digest-bound manifest with a separate publisher key and rejects stale or mismatched image manifests.

## Findings

### P1 — GitHub Actions are referenced by mutable version tags

The inspected workflows use action references such as `actions/checkout@v7`, `actions/setup-python@v6`, `actions/upload-artifact@v6/v7`, and `actions/attest-build-provenance@v3`. Other workflow files still use older major tags such as checkout@v4 and setup-python@v5. These are human-readable version tags, not immutable commit SHA references.

A tag can be moved by its publisher or compromised account. A workflow can then execute different third-party code without a repository commit changing. This is a supply-chain integrity gap, especially for workflows that receive write permissions or publish artifacts.

**Recommendation:** pin every action to a verified full 40-character commit SHA and retain the human-readable release in a comment. Use Dependabot to propose reviewed SHA updates. Verify each SHA against the official upstream release; do not copy unverified hashes.

### P1 — Python dependency resolution is not reproducible or hash-locked

`pyproject.toml` and the requirements manifests specify version ranges (for example `PyYAML>=6.0.3,<7.0`). CI installs from those ranges, optional workflow manifests add further unpinned transitive packages, and the Dockerfile installs directly from `requirements.txt`. No complete, generated, platform-aware lockfile with artifact hashes was identified in the inspected packaging paths.

The same source commit can therefore resolve different transitive package versions on separate builds. A compromised or newly malicious compatible release can enter a later build without a source change.

**Recommendation:** introduce a supported lock-generation process (including optional extras/platform groups), pin all transitive versions and require hashes for release/production builds. Keep developer convenience manifests separate from the hash-locked production install inputs. Verify the lock in CI and build Docker images from the same locked inputs.

### P1 — Write-capable model-alert workflow grants broad token permissions to the entire job

`hf-model-change-alerts.yml` declares `contents: write` and `issues: write` at workflow scope. The job checks out repository code, installs/runs Python, calls GitHub CLI, and updates a persistent baseline branch. These permissions are needed for the final write operations but are available throughout the earlier checkout, test and scout steps as well.

The workflow currently runs on schedule or manual dispatch rather than pull requests, which reduces exposure, but a compromised dependency or code path during an earlier step can access the job token before the intended write stage.

**Recommendation:** split read-only discovery/test work from the write operations. Give the writer job only the narrow permissions it needs, pass a validated artifact between jobs, and keep untrusted PR code away from the writer job. Consider a narrowly scoped GitHub App installation token for baseline updates.

### P2 — Desktop artifacts are unsigned and checksum files are not themselves an authenticity proof

The desktop workflow explicitly produces unsigned bundles. SHA-256 checksums detect accidental corruption only when the checksum manifest is obtained through a trusted channel. Provenance attestation is applied to the checksum manifest, rather than directly to each packaged file in the reviewed workflow. The SBOM is generated from the build environment and should be checked against the actual packaged contents.

**Recommendation:** attest the actual platform artifacts and SBOMs, verify provenance at download/install time, and sign release packages with a protected release identity. Make the release process verify that the attested subject digests match the distributed files.

### P2 — Docker build does not establish an immutable base image or hash-verified install

`docker/Dockerfile` uses `FROM python:3.11-slim` (mutable tag) and installs packages from un-hashed requirements. The image can change between builds even when Dockerfile source is unchanged.

**Recommendation:** pin the base image by digest, use hash-locked dependencies, and record the resulting image digest and SBOM in release provenance.

## Remediation status

- No code/workflow patch submitted in this pass. The action-pin and lockfile changes span many workflows and supported platform/dependency groups; guessing upstream action SHAs or introducing an incomplete lockfile would create false assurance.
- The audit records concrete gaps and a safe remediation sequence. A focused follow-up should first establish verified action SHA inventory and lock-generation strategy, then migrate workflows in controlled batches.
- No claim is made that the current build pipeline is compromised; these are integrity and blast-radius weaknesses.

## Validation status

- Source inspection completed for the files listed above and an inventory of workflow action references.
- Existing tests and workflows were inspected but **not executed**.
- No dependency resolution, package vulnerability scan, malicious package simulation, GitHub Actions execution, provenance verification, signature verification, or production release was run.
- This documentation PR is separate from implementation; no merge or deployment is performed.
