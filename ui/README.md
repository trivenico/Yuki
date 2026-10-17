# User interface

The current supported interface is the guarded terminal entry point in `main.py`. It exposes status, registered skills, and a one-action approval prompt.

A graphical dashboard and Textual TUI are not implemented yet. Future UI work should display the exact action, target/workspace, risk, and one-time approval decision; provide cancellation and an audit view; and never bypass `core.permissions.PermissionGate`.
