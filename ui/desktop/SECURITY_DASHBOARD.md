# Desktop security dashboard panel

`ui.desktop.security_dashboard.SecurityDashboardPanel` provides a passive Qt
panel for the read-only desktop security monitor.

- Creating the panel does not instantiate sensors, start workers, open devices,
  create timers, or collect data.
- Monitoring starts only after the user presses **Start monitoring** and
  confirms the session-specific disclosure.
- The owner must run sensor work on a background worker and deliver snapshots
  back to the Qt event thread.
- Stop and refresh are explicit callbacks supplied by the owning window.
- The panel accepts only snapshots marked `read_only=True`.
- Detector-provided free-form alert messages are never rendered. Known alert
  types map to fixed user-facing explanations.
- No process termination, firewall mutation, persistence, or telemetry upload
  is implemented by this panel.

This is a reusable UI component, not a claim that the desktop security monitor
is already wired into the main Yuki window. Integration follows after the
sensor PRs and lifecycle behavior are reviewed and merged.
