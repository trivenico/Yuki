# Portable release-candidate build orchestration

\`scripts/portable_release_build.py\` connects the existing verified-input,
runtime assembler, bundle assembler, portable entrypoint, manifest generator,
and native metadata audit into one host-native build command.

It intentionally produces an **unsigned release candidate**, not a distributable
or production-ready release. It does not fetch runtime archives, resolve
dependencies, invent hashes, access signing keys, sign artifacts, or launch the
Yuki application.

## Trusted inputs

The release operator must obtain and independently review all of the following:

- A platform-specific runtime ZIP and its SHA-256 digest from the selected
  runtime publisher.
- A complete, exact-pin, SHA-256-hashed requirements lock, including the Yuki
  application wheel and its full transitive dependency closure.
- A wheelhouse containing only the exact wheels referenced by that lock.
- A universal pip bootstrap wheel, its SHA-256 digest, and exact version.
- The runtime interpreter's relative path inside the extracted archive.

Do not derive a runtime digest from the same untrusted download at build time.
The expected digest must come from an independently trusted release record.
Do not put signing private keys in this repository, the wheelhouse, or bundle.

## Host-native build

Run on the same operating-system family as the target. The native audit uses
trusted host inspection tools (\`readelf\`, \`otool\`, or \`dumpbin\`) and refuses
cross-OS target labels. Use a separate clean build host for each OS.

Example Linux invocation (all paths and digest values below are placeholders,
not published Yuki release inputs):

\`\`\`bash
python -m scripts.portable_release_build \\
  --runtime-archive ./inputs/python-runtime.zip \\
  --runtime-sha256 <independently-pinned-runtime-sha256> \\
  --python-relative-path bin/python3 \\
  --requirements-lock ./inputs/requirements-linux.lock \\
  --wheelhouse ./inputs/wheelhouse-linux \\
  --pip-wheel ./inputs/pip-25.0-py3-none-any.whl \\
  --pip-sha256 <independently-pinned-pip-wheel-sha256> \\
  --pip-version 25.0 \\
  --work-root ./build-work \\
  --output-root ./release-output \\
  --platform-tag linux-x86_64 \\
  --runtime-version 3.12.9
\`\`\`

The output parent (here, \`./release-output\`) must already exist. The work
root must exist and be separate from the output parent. Choose the interpreter
path and platform tag from the actual runtime artifact; do not copy the example
values blindly.

The command verifies the runtime archive digest, safely extracts it, installs
the hash-locked wheel closure offline using the pinned pip wheel, assembles
\`runtime/\` and \`site-packages/\`, adds the portable entrypoint, writes the
deterministic unsigned inventory manifest, and runs the native metadata audit.
It stops if the audit reports a metadata failure.

## Output and release gates

The candidate is written to:

\`\`\`text
<output-root>/yuki-portable/
  portable_entry.py
  runtime/
  site-packages/
  runtime-manifest.json
\`\`\`

The JSON report explicitly marks \`signed=false\` and \`release_ready=false\`.
The manifest does not have a detached signature. Do not distribute this
candidate as an authenticated USB release.

Before production release, still required:

1. Protected signing workflow, key custody, rotation and revocation policy.
2. Independently distributed authenticated host bootstrap/installer.
3. Anti-rollback release policy and signature verification on the host.
4. Actual USB launcher integration for each supported OS.
5. Clean-machine relocation, native dependency resolution, and application
   health tests on every supported OS/architecture/Python target.
6. Reproducibility comparison and release provenance/attestation.

The Windows audit inventories PE imports but does not prove DLL resolution.
Linux/macOS metadata audits do not prove ABI compatibility or full transitive
system-library closure. A green parser test suite is not a substitute for
building and testing real release artifacts.
