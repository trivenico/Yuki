# Portable release manifest generation

`scripts/portable_manifest.py` creates the deterministic inventory manifest
consumed by the host-installed trusted bootstrap. It hashes every regular file
under an already assembled bundle, sorts entries by path, rejects symlinks and
case/Unicode-normalized path collisions, and writes the exact schema expected
by `yuki_trust.portable_bootstrap`.

Example:

```bash
python -m scripts.portable_manifest \
  --bundle build/yuki-portable-linux-x86_64 \
  --platform linux-x86_64 \
  --runtime-version 0.1.0
```

The output is intentionally **unsigned** and uses
`artifact_kind=unsigned-portable-runtime`. The signature file is not created.
The manifest generator never handles or stores a private signing key. A later
protected release-signing job must sign the exact bytes of
`runtime-manifest.json` with an externally managed Ed25519 key, provision the
matching public key to the host installer, and change the artifact kind/schema
only through a reviewed release process.

Run tests with:

```bash
python -m unittest tests.test_portable_manifest
```

This tool inventories a bundle; it does not assemble the runtime, merge
dependency directories, create launchers, or prove clean-machine portability.
The bundle must already have its final file layout before manifest generation.
