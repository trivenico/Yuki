# Portable runtime ZIP extraction

The build pipeline now has a standard-library-only ZIP extraction primitive:

```bash
python scripts/portable_archive.py \
  --archive vendor/python-runtime.zip \
  --destination build/runtime/python
```

Security and reliability properties:

- accepts only regular files and directories; rejects symlinks, special files,
  encrypted members, absolute paths, traversal, backslashes and drive/ADS paths;
- rejects duplicate, case-folded and Unicode-normalized path collisions, plus
  file/directory parent collisions;
- enforces member-count, per-file expanded-size, total expanded-size and
  compression-ratio limits;
- writes into a newly-created sibling staging directory and publishes it with
  an atomic rename only after every member has been fully read and size-checked;
- preserves ordinary executable bits while stripping setuid/setgid/sticky bits;
- never executes extracted files.

The extractor intentionally supports ZIP only. Runtime vendors commonly
publish different archive formats by operating system; additional formats
must get separate, equivalently hardened extractors and adversarial tests.
The caller must verify the archive's trusted SHA-256 using
`scripts/portable_runtime_inputs.py` before extraction. This is not a complete
runtime builder, does not install dependencies, and does not authenticate the
release by itself. Destination parent directories and the build host remain
trusted inputs.
