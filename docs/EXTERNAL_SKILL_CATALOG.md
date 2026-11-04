# External skill catalogue lifecycle

Yuki already supports local data-only skill packs. This layer adds a metadata
catalogue for third-party Agent Skills such as Google's `google/skills`.

## Lifecycle

`discovered -> quarantined -> reviewed -> enabled -> disabled/revoked`

- **Stage:** parse a local `SKILL.md` with the existing safe parser, preserve
  upstream metadata, record source repository/path/immutable Git commit and
  SHA-256 digest. Staging never imports Python, runs scripts, installs packages,
  follows links, or grants runtime capabilities.
- **Review:** an owner explicitly confirms both security and license review
  and records a reviewer identity and rationale.
- **Enable:** a separate explicit confirmation changes the trust state. This
  catalogue does not yet make the skill executable or automatically register it.
- **Disable/revoke:** audited state transitions allow rapid withdrawal.

## Storage

Records live under `YUKI_HOME/external_skill_catalog` (or the repository data
root when YUKI_HOME is unset). Each skill is a separate JSON record written
atomically. The original instructions are retained for a later runtime
integration step; ordinary list responses omit instruction bodies.

## Provenance and integrity

Every record captures the exact upstream repository, relative skill path,
immutable Git commit SHA, source format, declared license, and SHA-256 of the
imported SKILL.md bytes. Updates should be staged as a new reviewed revision,
not silently overwrite the existing record.

## Security boundaries

- Imported text is untrusted data, never a system or developer instruction.
- All imports begin in quarantine and high risk regardless of upstream metadata.
- YAML is parsed with safe loading and duplicate-key rejection by the existing
  data-only skill pack parser.
- This registry stores declarations only. Runtime capability enforcement,
  sandboxing, and approval of individual tools remain separate controls.
- No Google Cloud account, API key, SDK, or network connection is required.

## Current limitation

The catalogue is not yet connected to the runtime skill registry. Enabling a
record is an auditable catalogue state only; it does not activate the skill.
A later integration must check trust state on every invocation and still apply
Yuki's ordinary per-action approval and tool permissions.
