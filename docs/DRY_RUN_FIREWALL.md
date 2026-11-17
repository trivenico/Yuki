# Firewall planning safety

Yuki's current firewall component is a dry-run planner only.

- Firewall planning is disabled by default.
- The constructor rejects `dry_run=False`; there is no live firewall execution path.
- A plan requires an explicit approval callback that returns the boolean `True`.
- Plans are held in memory and capped by a configurable limit (1–1,000).
- Removing a plan removes only the local in-memory record.
- No subprocess, shell, administrator elevation, firewall command, or host rule mutation is used.
- Special-purpose IP addresses are rejected.
- A plan is not an applied firewall rule and does not protect the host by itself.

Live firewall support must be designed separately with durable expiry, crash recovery,
OS-specific permission boundaries, exact rule ownership, rollback, and integration
tests on each supported operating system. It must not be enabled by changing a flag.
