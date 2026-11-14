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
  --python runtime/bin/python3 \
  --entrypoint portable_entry.py
```

Windows uses the same signed Python script but the platform-specific interpreter
path, commonly `runtime/python.exe`:

```powershell
yuki-portable-bootstrap.exe --bundle E:\\Yuki\\yuki-portable --public-key C:\\ProgramData\\Yuki\\trust\\release-ed25519.pem --expected-platform windows-x86_64 --python runtime/python.exe --entrypoint portable_entry.py
```

The host installer pins both relative paths for each supported runtime. The
bootstrap requires both interpreter and script to appear in the signed inventory,
then invokes the staged interpreter with `-I` (isolated mode). It never executes
the Python script directly or trusts an interpreter path supplied by the bundle.
The exact interpreter path must match the reviewed runtime archive and host
installer policy.

The entrypoint adds only the bundle's `site-packages/` and root to
`sys.path`. On Windows it registers exactly two documented DLL search
directories when present: `runtime/` (required) and `native-libs/` (optional).
Registration handles are retained for process lifetime. It does not scan
arbitrary directories, register the working directory, add host site-packages,
read `PYTHONPATH`, fetch dependencies, or execute unsigned code. The bootstrap
must authenticate the interpreter, this script, the application, and every
dependency before launch.

Linux and macOS are intentionally not modified by this Windows-specific
loader setup. Their native libraries need reviewed loader metadata (for example,
relative ELF runpaths or Mach-O `@loader_path` / `@rpath`) and clean-machine
validation. Codecs, GPU drivers, and OS services may remain external requirements.
