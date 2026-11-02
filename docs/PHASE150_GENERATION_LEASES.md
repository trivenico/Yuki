# Phase 150 — Generation leases

Conversation generation uses database-backed leases so independent application workers cannot start duplicate turns for the same conversation. Leases expire after ten minutes and are released with an unguessable token. This remains SQLite-oriented and requires operational validation before multi-host deployment.