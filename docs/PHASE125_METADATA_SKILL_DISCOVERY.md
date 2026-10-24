# Phase 125 — Metadata-only skill discovery

Adds `skills/metadata_discovery.py` as a safe discovery path for candidate skill
manifests. It does not import Python modules, execute entrypoints, or silently
register new capabilities.

## Manifest format

Place a `*.skill.json` file under a trusted discovery root:

```json
{
  "name": "weather",
  "description": "Get a weather summary",
  "risk": "low",
  "permissions": ["network"],
  "entrypoint": "weather.py"
}
```

The scanner bounds manifest size and candidate count; validates names, description,
risk, permission names and relative entrypoint paths; rejects duplicate names;
and records a SHA-256 digest so reviewers can detect a changed manifest.

## Workflow

1. `discover_skill_manifests(root)` returns candidates with `pending_review` status.
2. A trusted review process checks the source, permissions, and integrity.
3. A trusted bootstrapper instantiates reviewed code independently.
4. `register_approved_skill(..., approved_names=..., verified_sha256=...)` registers
   only if the name was explicitly approved and the manifest digest still matches.

The module never imports the entrypoint. A manifest is metadata, not proof that its
Python code is safe. Permission declarations are requests for review, not an OS
sandbox. Legacy `skills.discovery.discover_skill_classes()` still imports modules;
do not use that legacy helper on untrusted skill directories.

## Verification

```bash
python -m unittest tests.test_metadata_skill_discovery -v
python -m unittest discover -s tests -v
```

## Not yet integrated

This is a safe discovery primitive, not automatic runtime wiring. It does not
change startup registration, install dependencies, grant capabilities, or make
untrusted code safe. Integrate it only after CI and review, with a persistent
approval store and execution permissions enforced by the runtime.
