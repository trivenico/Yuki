# Phase 236 — Static Artifact Inventory

**Status:** Implementation committed; CI verification pending.

## Capability

Yuki can inventory an explicitly supplied, existing application directory without executing or parsing target content. The scanner records sorted relative paths, byte sizes, SHA-256 file digests, broad file categories, and known manifest filenames.

## Safety contract

- Root must resolve to an existing directory.
- Directory traversal is read-only and deterministic.
- Symlinks are skipped; symlinked files are never followed.
- Special files are skipped.
- Default ignored directories: VCS metadata, Python caches/virtual environments and node_modules.
- Per-file, total-byte, file-count and read-chunk limits are enforced.
- File identity and metadata are checked before and after hashing to detect common replacement or mutation races.
- Manifest files are identified by name only; their contents are not parsed or executed.
- No shell, network, package manager, dynamic loader or target process is invoked.

## API

`reverse_engineering.artifact_inventory.inventory_artifact_root(root, ...limits)`

Returns a structured inventory with file records, manifest paths, byte counts, skipped-entry counts, explicit coverage and limitations. The caller should wrap the result in the Evidence envelope and persist the resulting record using the evidence store planned in a later phase.

## Deliberate limitations

This is a static file inventory, not source-code comprehension, dependency resolution, malware verdict, binary decompilation or runtime tracing. Default excluded directories mean coverage is partial. A caller may supply additional simple directory names to exclude; the API does not allow removing mandatory exclusions.

## Tests

Regression tests cover hashing, manifest identification, excluded directories, symlink handling, file-count and byte limits, invalid roots and directory symlink roots. CI results determine verification status.
