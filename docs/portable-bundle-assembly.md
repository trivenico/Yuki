# Portable bundle assembly

`scripts/portable_bundle.py` combines two already-verified build outputs into
one predictable directory:

- `runtime/`: the extracted embedded Python runtime.
- `site-packages/`: the offline-installed, hash-locked Yuki application and
  dependency tree.

The tool copies only regular files and ordinary directories, rejects symlinks
and special files, preserves ordinary permission bits, and atomically publishes
the destination only after both trees have been copied successfully. Inputs
are never executed. The output is intentionally unsigned.

Example:

```bash
python -m scripts.portable_bundle \
  --runtime-root build/runtime \
  --dependency-root build/dependencies \
  --destination build/release/yuki-portable
```

Generate the release inventory only after all launchers, assets and platform
files have been added:

```bash
python -m scripts.portable_manifest \
  --bundle build/release/yuki-portable \
  --platform linux-x86_64 \
  --runtime-version 0.1.0
```

This stage does not merge site-packages into the interpreter, create a
platform-native launcher, sign the manifest, provision a host trust anchor,
or prove clean-machine portability. Those remain explicit release gates.
