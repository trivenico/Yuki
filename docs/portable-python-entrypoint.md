# Portable Python application activation

The bundle assembler preserves the embedded interpreter under `runtime/` and
the hash-locked Yuki wheel plus dependencies under `site-packages/`. This
entrypoint makes that layout importable without relying on host Python
configuration or ambient `PYTHONPATH`.

After assembling the bundle, copy the repository's `portable_entry.py` into
the bundle root **before** generating the final manifest and detached
signature. The entrypoint is therefore part of the signed inventory.

The independently installed host bootstrap should verify and stage the bundle,
then launch the platform's signed interpreter with this script:

Linux/macOS example:

```bash
yuki-portable-bootstrap \
  --bundle /media/YUKI/yuki-portable \
  --public-key /etc/yuki/trust/release-ed25519.pem \
  --expected-platform linux-x86_64 \
  --entrypoint runtime/bin/python \
  -- -I portable_entry.py
```

Windows uses the same signed Python script but the platform-specific interpreter
path, commonly `runtime/python.exe`; the exact path must match the reviewed
runtime archive and host installer policy.

The entrypoint adds only the bundle's `site-packages/` and root to
`sys.path`. It does not add host site-packages, read `PYTHONPATH`, fetch
dependencies, or execute unsigned code. The bootstrap must remain the trust
boundary and must authenticate the interpreter, this script, the application,
and every dependency before launch.

This is a path activation layer, not proof of full portability. Native
extensions may still depend on platform system libraries, runtime-specific
loader paths, audio/video codecs, GPU drivers, or OS services. Those require
separate platform smoke tests on clean machines.
