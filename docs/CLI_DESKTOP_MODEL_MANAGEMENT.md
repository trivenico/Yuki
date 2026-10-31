# Shared CLI and desktop model management

## CLI

```bash
python yuki_cli.py hardware
python yuki_cli.py models
python yuki_cli.py models --task coding
python yuki_cli.py models --task reasoning
```

Commands are read-only, emit JSON, and do not initialize an LLM provider, download weights or activate a model.

## Shared services

- `core/model_manager.py` owns catalog and hardware-report behavior.
- `interfaces/desktop/model_api.py` is the stable desktop-facing facade.
- CLI and future native desktop consume the same service; model policy is not duplicated.

## Desktop status

A native Electron, Tauri, Qt or platform-native shell is not currently present. The optional Textual console is a terminal UI, not a desktop app. A future shell must consume shared services, use OS credential storage and preserve permission, approval and audit boundaries.

The current milestone provides discovery only. Download, install, update, removal and activation need a transactional lifecycle service with pinned revisions, disk-space checks, cancellation, integrity validation and explicit user approval.
