# Phase 98 — third-party skill risk classification and import gating

## Inspiration

Reviewed SnailSploit/Claude-Red (MIT) as a reference for structured SKILL.md knowledge packs, metadata-driven discovery, specialist categories, concise task guidance, and repository-level security documentation.

Yuki already supports Agent Skills-style SKILL.md import, GitHub revision pinning, metadata-only installation, linting, and guarded invocation. This phase hardens that existing path rather than adding a second installer.

Upstream: https://github.com/SnailSploit/Claude-Red

## Changes

- Add conservative risk classification to imported skill manifests.
- Unknown / legacy installed skill manifests default to medium risk.
- Imported skills never receive low risk automatically.
- Security-sensitive vocabulary (offensive operations, exploit development, credential harvesting, privilege escalation, persistence, evasion, exfiltration, keylogging, shellcode, phishing) triggers high risk.
- Imported InstalledPromptSkill now propagates manifest risk and requires invocation approval for medium/high.
- GitHub import resolves a commit SHA and fetches the exact SKILL.md before displaying approval preview.
- Preview includes risk, invocation approval requirement, lint findings, upstream SPDX license and license-file path when available, content SHA-256, pinned commit, and companion script candidates. License identifiers and paths are persisted in installed provenance metadata.
- Only the selected SKILL.md is installed. Companion scripts and reference assets are not copied or executed.
- CLI skill inventory exposes risk and approval requirements.
- Local SKILL.md imports receive the same conservative risk classification.
- Explicitly reject unknown risk levels in manifests.

## Security model

A skill is model guidance, not an authorization grant. The imported prompt is untrusted data and must not override system policy, runtime permissions, network/file allowlists, or user approval.

Risk classification is heuristic and intentionally conservative. It is not a complete semantic security review. The operator must inspect the exact pinned source and license before installation. The explicit CLI --yes option remains an operator-controlled approval mechanism.

The MIT license permits reuse subject to retaining the copyright and license notice in copies or substantial portions. This phase records the upstream SPDX identifier and license-file path, but does not yet copy the full license text into the installed extension. Therefore this is provenance tracking, not a complete redistribution-ready license bundle. Before redistributing imported skill content or bulk-vendoring upstream material, Yuki must preserve the applicable copyright and license notice alongside the copied content.

## Commands

    yuki skills add SnailSploit/Claude-Red --path Skills/utility/offensive-reporting
    yuki skills list
    yuki skills lint ./path/to/SKILL.md

The repository contains multiple skills; --path selects one skill directory. Review the printed preview before confirming installation.

## Limitations / follow-up

- No automatic bulk import of all upstream skills.
- No importing or executing upstream scripts, templates, binaries, or reference files.
- Heuristic risk classification can miss risky instructions or over-classify benign content.
- The current preview reports upstream repository license metadata and persists the license-file path, but does not copy the full license text into the installed extension; this is not yet a complete redistribution-ready license bundle.
- The importer does not independently resolve every nested license or establish legal compatibility.
- A successful import does not mean a skill is safe, accurate, or production-approved.
- Future work: enrich manifest provenance with immutable source URL, license evidence, content digest, review status, and approval actor; add policy tests for prompt-injection and tool-exfiltration instructions; make Desktop and CLI share the same extension data root.

## Verification

    python -m unittest tests.test_extension_store -v
    python -m unittest tests.test_skill_linter -v
    python -m unittest tests.test_approved_skill_runtime tests.test_approved_skill_wiring -v
    python -m unittest discover -s tests -v
