# Portable runtime build input gate

Before assembling a USB runtime, validate the exact runtime archive and a
platform-specific offline wheelhouse:

```bash
python scripts/portable_runtime_inputs.py \
  --runtime-archive vendor/python-runtime.zip \
  --runtime-sha256 <trusted-64-character-sha256> \
  --requirements-lock locks/core-linux-x86_64-py312.lock \
  --wheelhouse wheelhouses/core-linux-x86_64-py312
```

The command is intentionally a validation-only gate. It:

- hashes the supplied runtime archive and compares it with an independently
  supplied SHA-256 value;
- requires every dependency line to carry SHA-256 hashes;
- accepts only wheel files in the offline wheelhouse;
- verifies every locked requirement has at least one matching wheel digest;
- rejects wheelhouse files not referenced by the lock;
- rejects pip index options, URLs, local paths, and source distributions.

The validation-only invocation does not download, extract, install, execute, or sign anything. When both installation flags are supplied, the separate installation stage below executes the already-verified runtime interpreter and pip. The runtime
archive digest must come from a trusted release source, not from the same
untrusted download channel as the archive. Lock files must be generated and
reviewed per OS, architecture, Python ABI, and feature profile.

This is an input-integrity gate plus an optional offline-install step, not yet a complete runtime builder. The ZIP extractor currently supports ZIP only; it does not accept `.tar.gz` or `.tar.zst`. Separate hardened adapters are still required for runtime distributions published in other formats. The next stage must integrate supported archive adapters, install Yuki itself with its complete dependency closure, verify relocation, and run on clean machines without a host Python installation. Signed release manifests and the
independently installed host bootstrap remain separate trust requirements.


## Offline installation stage

After the runtime archive has been independently digest-verified and safely
extracted, the build process can install the reviewed dependency closure with
the interpreter from that verified runtime:

```bash
python scripts/portable_runtime_inputs.py \
  --runtime-archive vendor/python-runtime.zip \
  --runtime-sha256 <trusted-64-character-sha256> \
  --requirements-lock locks/core-linux-x86_64-py312.lock \
  --wheelhouse wheelhouses/core-linux-x86_64-py312 \
  --install-runtime-root build/runtime/python \
  --install-python build/runtime/python/bin/python \
  --install-target build/runtime/site-packages
```

The installer requires the interpreter to resolve inside the supplied verified runtime root, preventing an accidental fallback to the build host Python. It invokes that interpreter with isolated pip, `--no-index`,
`--find-links`, `--require-hashes`, and `--only-binary=:all:`; it does not
use package indexes or source distributions. The lock must contain the complete
dependency closure, exact versions, and hashes for the selected platform and
Python ABI. Pip itself validates wheel metadata, compatibility and hashes.
Installation runs with a timeout in a fresh sibling staging directory. The staging directory is atomically published as the final target only after pip succeeds; failed or timed-out installs are cleaned up and never appear as completed targets.

This stage executes the supplied runtime interpreter and pip, so it must only
be used after the archive digest has been checked against an independently
trusted value and the runtime has been extracted from that verified archive.
The test suite mocks the installer process; it does not claim a real packaged
runtime has been assembled or launched.
