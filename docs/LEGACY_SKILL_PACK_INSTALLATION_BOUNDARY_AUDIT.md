# Legacy skill-pack installation and persistence boundary audit

Date: 2026-11-28

## Reachability

The root compatibility terminal exposes `/skill install PATH`. The handler
calls `install_skill_pack()`; `SkillRegistry.defaults()` loads installed
packs from `installed_skill_directory()`, which resolves to
`data_root()/skill_packs`. In a source checkout, `data_root()` can be the
repository root; this is not automatically the canonical private profile.
The installed CLI's separate conversation-only surface is not evidence that
this legacy command is unreachable.

## Current controls

- Imports are data-only JSON or bounded Markdown/YAML frontmatter; the loader
  rejects unsupported fields, duplicate keys, aliases, and oversized input.
  Imported packs are marked high-risk and remain subject to the guarded skill
  approval runtime. Pack instructions are untrusted guidance, not executable
  plugin code.
- Source files are required to be regular, non-symlink files. Installed
  manifests are validated and filenames must match the pack identifier.
- Installation uses a temporary file, fsync, and publication without
  intentionally overwriting an existing manifest.

## Boundary gaps

- The default install directory is under `data_root()`, not necessarily the
  canonical owner-private profile. `mkdir(parents=True)` does not validate
  existing ancestor permissions or reject a symlinked destination chain.
  The loader resolves the directory before enumeration, so this source path
  does not establish a no-follow guarantee for the configured directory.
- The per-file JSON lock and canonical MaintenanceFence are not used. An
  install/remove/load can race profile backup or other pack operations.
- The no-overwrite check is a check followed by `os.replace`; another
  installer can publish the same target between those operations. The second
  existence check narrows but does not eliminate the race. Concurrent
  installation therefore is not proven to be create-only.
- Removing a pack does not coordinate with a concurrent registry load or
  backup. No claim of a stable installed-pack snapshot is made.

## Decision

No runtime change was made. A correct fix must preserve source-checkout
compatibility while defining an owner-private destination, no-follow path
validation, a no-replace publication primitive, and shared-fence participation
for install/remove/load and backup. Tests should cover concurrent same-name
installs, symlinked directories, fence races, and registry snapshot consistency.
Do not treat data-only packs as trusted merely because they cannot import code:
their instructions remain untrusted input to the model and require the existing
approval boundary.

No pack files or directories were inspected or modified; no skill was
installed, removed, or invoked. No local tests were run.
