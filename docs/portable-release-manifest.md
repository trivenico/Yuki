# Portable release manifest generation

`scripts/portable_manifest.py` creates a deterministic SHA-256 inventory
consumed by the host-installed trusted bootstrap. It hashes every regular
file under an already assembled bundle, sorts entries by path, rejects
symlinks and case/Unicode-normalized path collisions, and writes the exact
schema expected by `yuki_trust.portable_bootstrap`.

Example:

```bash
python -m scripts.portable_manifest \
  --bundle build/yuki-portable-linux-x86_64 \
  --platform linux-x86_64 \
  --runtime-version 0.1.0
```

The manifest uses the stable artifact type
`artifact_kind=portable-runtime`. Authenticity is not inferred from that
field: the manifest is unsigned until a protected release process creates the
detached Ed25519 signature over the exact manifest bytes. The signature file
is not created here. Private signing keys must remain outside the repository
and bundle; the matching public key must be provisioned to the host installer
through an independently trusted channel.

Run tests with:

```bash
python -m unittest discover -s tests -p 'test_portable_manifest.py' -v
```

This tool inventories a bundle; it does not assemble the runtime, merge
dependency directories, create launchers, sign artifacts, or prove
clean-machine portability. The bundle must have its final file layout before
manifest generation.
