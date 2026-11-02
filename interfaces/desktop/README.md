# Desktop integration boundary

There is no native Electron, Tauri, Qt or platform-native desktop shell in this repository yet. The Textual supervision console is a terminal UI, not the desktop application.

The future desktop client should consume `model_api.py` and the shared application services. It must not duplicate model routing, hardware detection, model selection, memory, task orchestration or permission checks.

Required screens: chat, model catalog, hardware summary, provider status, installed-model inventory, task approvals, audit history, settings and diagnostics. Model download/install/remove actions must be explicit, cancellable, revision-pinned, integrity-checked and approval-gated.

The desktop shell must use OS credential storage, keep model weights and user data outside the source tree, and enforce the same permission and audit policy as the CLI.
