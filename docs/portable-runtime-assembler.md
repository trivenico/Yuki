# Portable runtime assembly

The assembler binds the existing portable build primitives into one offline
operation:

1. Verify the runtime ZIP against a SHA-256 digest supplied independently by
   the trusted build/release configuration.
2. Copy the archive into a private temporary snapshot while hashing that exact
   byte stream; compare it with the trusted digest, then extract only that
   immutable snapshot using the bounded, traversal-resistant ZIP extractor.
3. Resolve a caller-selected relative interpreter path inside the extracted
   runtime; reject traversal, symlinks and paths resolving outside the runtime.
4. Install the fully pinned dependency closure using that interpreter with
   pip isolated, offline, hash-required and binary-wheel-only settings.
5. Remove the newly extracted runtime if dependency installation fails.

Example API use:

```python
from pathlib import Path
from scripts.portable_runtime_assembler import assemble_portable_runtime

report = assemble_portable_runtime(
    archive=Path("vendor/python-runtime.zip"),
    expected_sha256=TRUSTED_RUNTIME_SHA256,
    runtime_root=Path("build/runtime"),
    python_relative_path="python/bin/python",
    requirements_lock=Path("locks/linux-cp312.lock"),
    wheelhouse=Path("wheelhouses/linux-cp312"),
    dependency_target=Path("build/dependencies"),
)
```

The archive digest must come from a trusted release manifest or build
configuration, not from the archive itself. Extraction re-hashes and snapshots
the source bytes before parsing, so replacing the archive path after the
initial validation cannot silently change the bytes that are extracted. The selected interpreter must
already exist in the verified runtime and provide pip. The lock must contain
the complete platform/Python-specific dependency closure and hashes for every
wheel. No downloads occur and the Yuki application is not launched.

This is an assembly primitive, not a signed end-user installer or a clean
machine portability guarantee. Runtime formats other than ZIP need separate
hardened adapters. Production integration still needs signed release metadata,
anti-rollback/key rotation, a protected host trust anchor, platform-specific
runtime acquisition, pip availability validation/bootstrap, and clean-machine
launch tests. The build host and destination parent directories are trusted.
