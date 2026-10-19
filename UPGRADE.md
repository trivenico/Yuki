# Yuki Upgrade, Migration & Rollback Guide

This guide describes how to safely upgrade Yuki, run migrations, or roll back releases.

## Upgrade Procedures

1. **Pull or Install Latest Release**:
   ```sh
   git pull origin main
   pip install -e .
   ```

2. **Run Diagnostics**:
   ```sh
   yuki doctor
   ```

3. **Schema Migrations**:
   Persistent stores (SQLite databases and JSON memory stores) use atomic writes and forward-compatible schema checks. Migration steps execute automatically upon initialization.

## Rollback Procedures

If rolling back a release (e.g. from `v0.2.0` to `v0.1.0`):
1. **Backup Workspace Data**:
   Ensure `yuki_workspace/` is backed up before downgrading.
2. **Re-install Selected Tag**:
   ```sh
   git checkout v0.1.0
   pip install -e .
   ```

## Deprecation Policy

Configuration options marked for deprecation will emit a warning for one minor version prior to removal.
