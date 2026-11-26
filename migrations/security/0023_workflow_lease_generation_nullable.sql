-- WI-6U13: a lease generation exists only while a worker owns an active lease.
-- Terminal, queued, and interrupted tasks clear the fencing generation; keep the
-- monotonic generation while running, but permit NULL after ownership is released.
ALTER TABLE workflow_runs
    ALTER COLUMN lease_generation DROP NOT NULL;
