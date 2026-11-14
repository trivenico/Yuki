# Portable native loader audit

Run the static audit against an assembled bundle on its target operating
system:

```bash
python -m scripts.portable_native_audit --bundle-root ./yuki-portable --platform linux
python -m scripts.portable_native_audit --bundle-root ./yuki-portable --platform macos
python -m scripts.portable_native_audit --bundle-root .\yuki-portable --platform windows
```

The tool inspects native binary metadata without loading or executing the
application. Linux uses `readelf -d` and rejects non-`$ORIGIN` RPATH/RUNPATH
entries. macOS uses `otool -l` and `otool -L`, requiring relative
`@loader_path` / `@executable_path` runpaths and rejecting absolute
non-system dependency install names. Windows uses `dumpbin /DEPENDENTS` to
inventory imported DLL names.

## Important limitations

- Windows dependency inventory is not resolution proof; a separate clean
  Windows launch test is required to prove DLL search behavior.
- Linux and macOS checks validate metadata policy, not ABI compatibility,
  transitive dependency closure, code signatures, or system-library versions.
- A clean-machine launch test and runtime health check remain mandatory.
- Inspection tools must come from the trusted build environment. The inspected
  bundle is treated as untrusted input and is never executed by this audit.
- An empty native-binary inventory fails closed; a portable runtime is expected to contain at least its platform interpreter executable.

Use the audit as a release gate alongside signed inventory verification,
hash-locked wheel installation, and per-platform clean-machine tests.
