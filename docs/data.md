# Yuki Data Management & Data Erasure Guide

This document describes where Yuki stores personal data and how to manage or delete it.

## Storage Locations

All local persistent state is contained within the configured workspace directory (`./yuki_workspace` by default):

- `yuki_memory.json`: Long-term lexical notes and memory entries.
- `yuki_reminders.json`: Saved and due reminders.
- `yuki_token_usage.json`: Local token consumption accounting logs.
- `approval_audit.jsonl`: Append-only audit records of approval requests and decisions.
- `heartbeat_state.sqlite3`: Proactive heartbeat check timestamps and state.
- `logs/yuki.log`: Local rotating application diagnostics log files.

## Data Deletion & Purging Commands

- **Clear Memory**: `/memory clear` (requires human approval)
- **Repair / Verify Memory**: `/memory doctor`
- **Purge Delivered Reminders**: `/reminders purge`
- **Complete Erasure**: Delete the `yuki_workspace/` directory.
