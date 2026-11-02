# Third-party notices

## OpenJarvis benchmark statistics

- Project: OpenJarvis — https://github.com/open-jarvis/OpenJarvis
- Source file: `src/openjarvis/bench/_stats.py`
- Source revision: `336a68ac3b6206e3f3037c5c6d9d40722c4ab4cb`
- Reused in Yuki: `providers/evaluation_stats.py`
- Copyright: Copyright 2025 The OpenJarvis Authors.
- License: Apache License 2.0.
- Changes: The upstream statistics helper is copied with its implementation preserved and an attribution header added. Yuki wires it into `providers/model_evaluation.py` to expose latency distribution metrics. The full license text is in `licenses/Apache-2.0-OpenJarvis.txt`.

OpenClaw is used as a format and architecture reference for the Agent Skills Markdown importer; no OpenClaw executable source files are copied. Hermes Agent contributes the adapted systematic-debugging skill documented below. Its MIT license and source attribution are retained.

## Hermes Agent systematic-debugging skill

- Project: Hermes Agent — https://github.com/NousResearch/hermes-agent
- Source file: `skills/software-development/systematic-debugging/SKILL.md`
- Source revision: `0f3fcacd3f92d02d163789b4c3441b6388f8753d`
- Reused in Yuki: `skills/upstream/hermes/systematic-debugging/SKILL.md`
- Copyright: Copyright (c) 2025 Nous Research.
- License: MIT License; full text in `licenses/MIT-Hermes-Agent.txt`.
- Changes: Adapted the root-cause debugging workflow for Yuki, removed Hermes-specific tool calls and unsupported delegation examples, added Yuki's actual verification and permission boundaries. The bundled skill is opt-in and imported through the data-only SKILL.md installer.
- The upstream skill itself credits obra/superpowers; that attribution is retained in the Yuki skill's frontmatter.

## Oracle OCI GenAI authentication library

- Project: OCI GenAI Auth — https://github.com/oracle-samples/oci-genai-auth-python
- Source revision: `main` as inspected on 2026-10-30; packaged upstream version pinned to `oci-genai-auth==1.1.1`.
- Reused in Yuki: dependency used by `providers/oci_iam.py`; Yuki does not copy Oracle's signer implementation.
- Copyright: Copyright (c) 2026 Oracle and/or its affiliates.
- License: Universal Permissive License (UPL), Version 1.0; full text retained in `licenses/UPL-1.0-Oracle-oci-genai-auth.txt`.
- Changes: Yuki adds its own OCI endpoint validation, model request/response bounds, explicit project header, provider-factory integration, and tests. Oracle's library supplies OCI IAM signing and credential refresh.
- Dependency manifest: `requirements-oci.txt`.
