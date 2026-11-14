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


## Host-installed launch policy

For end-user launches, install the host launcher and its policy independently
of the removable drive. The policy is a small JSON file managed by the host
installer; it pins the public trust anchor, exact platform identifier, bundled
interpreter path, and entrypoint path. The USB bundle cannot override these
values.

Example host policy (the installer must replace the illustrative paths with
the reviewed paths for the target runtime):

```json
{
  "schema_version": 1,
  "public_key": "/etc/yuki/trust/release-ed25519.pem",
  "expected_platform": "linux-x86_64",
  "python": "runtime/bin/python3",
  "entrypoint": "portable_entry.py",
  "anti_rollback_state": "/var/lib/yuki/release-state.json"
}
```

Launch using the host-installed command:

```bash
yuki-portable-launch --bundle /media/YUKI/yuki-portable --policy /etc/yuki/portable-policy.json -- --diagnostic
```

The host launcher rejects policy files and trust anchors located inside the
removable bundle, symlinked files, ambiguous JSON, unknown fields, noncanonical
bundle-relative paths, and (on POSIX) policy/key files not owned by the current
user or writable by group/other. Windows ACL enforcement is an installer
responsibility and has not yet been implemented or validated by this library.
The launcher then delegates signature, inventory, platform, staging, and
isolated-interpreter checks to the trusted bootstrap. The policy also pins a
persistent anti-rollback state file outside removable media. The signed manifest
contains a monotonically increasing `release_sequence`; the bootstrap commits
that sequence and exact manifest digest after staged verification and immediately
before executing the signed interpreter.

This is a launch-policy library and CLI foundation, not yet a signed OS
installer. The production installer must create the policy and trust anchor
with restrictive ownership/ACLs, pin reviewed runtime paths, and prevent
unprivileged modification. Key rotation/revocation, protected release signing, Windows ACL provisioning,
and clean-machine runtime tests remain separate release gates.
