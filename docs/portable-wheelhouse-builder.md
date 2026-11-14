# Portable wheelhouse builder

The wheelhouse builder turns a reviewed, platform-specific, hash-locked
requirements file into an offline directory of binary wheels. It is a
networked build-time operation; it does not install or execute downloaded
packages.

Example for CPython 3.12 on x86-64 manylinux:

```bash
python scripts/portable_wheelhouse.py \
  --lock locks/core-linux-x86_64-py312.lock \
  --destination build/wheelhouses/core-linux-x86_64-py312 \
  --platform manylinux_2_28_x86_64 \
  --platform manylinux2014_x86_64 \
  --python-version 3.12 \
  --implementation cp \
  --abi cp312 \
  --abi abi3
```

For an internal package index, provide an HTTPS URL without embedded
credentials using `--index-url`. Supply credentials through the build
environment's approved secret mechanism; do not commit them in lock files or
URLs.

## Guarantees

- Requires exact `==` pins and SHA-256 hashes on every requirement line. Target locks must not use environment markers; generate a separate fully resolved lock for each target.
- Requires the Yuki application wheel to be explicitly pinned in the lock.
- Uses pip `download` with `--require-hashes`, `--only-binary=:all:`,
  `--no-deps`, and explicit target platform, Python version, implementation
  and ABI.
- Does not resolve missing dependencies. The reviewed lock must already
  contain the complete dependency closure and hashes for the target.
- Validates that every lock line has a matching wheel and rejects unreferenced
  files, duplicate-content wheels, non-wheel files and symlinks.
- Builds into a private sibling staging directory and atomically publishes
  only after validation succeeds.
- Never installs or imports the downloaded packages.

## Important limitations

This tool does not generate a lock file or prove that a lock is complete.
Lock generation and review remain a separate reproducible release-engineering
step. Run it once per supported OS, architecture, Python ABI and feature
profile. The selected platform and ABI tags must match the intended runtime.
A successful wheelhouse build does not prove that native libraries, external
executables, model assets, drivers or OS-level dependencies are bundled.

The builder uses the current build host's Python and pip to perform downloads.
Cross-platform selection is delegated to pip's target-tag support; build
Windows, macOS and Linux wheels in their native CI environments where
platform-specific package behavior requires it. Treat the package index and
build environment as trusted inputs, and retain the lock, wheel hashes and
build logs as release artifacts.
