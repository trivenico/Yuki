# Phase 46 — Runtime doctor and release readiness

## Readiness verdict
**Yuki is a development prototype, not production-ready.** The README already documents important gaps: no production-grade native tool-calling/ReAct runtime, no autonomous project builder, incomplete voice loop, and security controls needing further independent review. Provider routing, Hugging Face discovery, evaluation and recent audit changes are still separate open PRs and do not become usable just because their branches exist.

## Change
`python -m core.doctor` checks Python version, validated configuration, provider construction and the provider's availability response. `python -m core.doctor --json` emits machine-readable output. The doctor does not send prompts, install models, start services, run skills or disclose exception messages/secrets.

A passing result means the configured provider reports itself available. It **does not** prove a successful inference round trip, correct model output, safe tool execution, or production readiness. The doctor exits non-zero when configuration/provider readiness fails or remains unverified.

## Suggested local setup
1. Install Python 3.11+.
2. Create and activate a virtual environment.
3. Install core dependencies: `python -m pip install -r requirements.txt`.
4. For local inference, install Ollama separately, pull a compatible model, and configure `YUKI_OLLAMA_MODEL` (and `YUKI_PROVIDER=ollama` only if supported by the config schema; inspect `config.yaml` before setting).
5. Run `python -m core.doctor` and `python -m unittest discover -s tests -v`.
6. Start Yuki using the repository's documented launcher and try `/ask Explain a Python function` with the configured provider.
7. Do not enable Gmail, plugin discovery, firewall, packet sniffing, exploit tooling, or remote sandbox access until each feature has been reviewed and explicitly configured.

## Release gates still outstanding
- Merge/rebase dependent PRs after resolving merge conflicts and reviewing CI.
- Run the full test suite on supported operating systems and verify results, not just workflow configuration.
- Perform a real local inference round trip and test timeout/error handling.
- Complete provider registry/router/factory integration and make UI capability reporting truthful.
- Add auth-bound session ownership before enabling conversation memory in a multi-user deployment.
- Independently review security-sensitive modules and deployment secrets/network boundaries.
- Define supported install/update process, backup/restore, logs/metrics, release artifact and rollback.

## Honest status
This phase adds a read-only diagnostic and tests; it does not claim to fix all defects or certify the application. The repository's main branch is the only basis for what a fresh checkout can run; open PR features remain unavailable until merged.