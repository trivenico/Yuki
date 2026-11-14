# Portable runtime build input gate

Before assembling a USB runtime, validate the exact runtime archive and a
platform-specific offline wheelhouse:

```bash
python scripts/portable_runtime_inputs.py \
  --runtime-archive vendor/python-runtime.tar.gz \
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

It does not download, extract, install, execute, or sign anything. The runtime
archive digest must come from a trusted release source, not from the same
untrusted download channel as the archive. Lock files must be generated and
reviewed per OS, architecture, Python ABI, and feature profile.

This is an input-integrity gate, not yet a complete runtime builder. The next
stage must safely extract supported runtime archives, install only with
`pip --no-index --require-hashes`, verify relocation, and run on clean machines
without a host Python installation. Signed release manifests and the
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
  --install-python build/runtime/python/bin/python \
  --install-target build/runtime/site-packages
```

The installer invokes that interpreter with isolated pip, `--no-index`,
`--find-links`, `--require-hashes`, and `--only-binary=:all:`; it does not
use package indexes or source distributions. The lock must contain the complete
dependency closure, exact versions, and hashes for the selected platform and
Python ABI. Pip itself validates wheel metadata, compatibility and hashes.
Installation runs with a timeout and into a new target directory.

This stage executes the supplied runtime interpreter and pip, so it must only
be used after the archive digest has been checked against an independently
trusted value and the runtime has been extracted from that verified archive.
The test suite mocks the installer process; it does not claim a real packaged
runtime has been assembled or launched.
