"""Comprehensive environment and capability diagnostics for Yuki."""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CORE_SUBSYSTEMS = (
    "core",
    "companion",
    "skills",
    "providers",
    "defense",
    "hackback",
    "voice",
    "ui",
    "knowledge",
    "automation",
)

OPTIONAL_LIBRARIES = {
    "PyYAML (config parsing)": "yaml",
    "faster-whisper (STT)": "faster_whisper",
    "pyttsx3 (TTS engine)": "pyttsx3",
    "openwakeword (Wake word)": "openwakeword",
    "textual (UI dashboard)": "textual",
    "playwright (Browser automation)": "playwright",
    "google-auth-oauthlib (Gmail)": "google_auth_oauthlib",
    "docker (Sandbox runner)": "docker",
}


def run_diagnostics():
    lines = []
    lines.append("=== Yuki Environment & Capability Diagnostics ===")
    lines.append(f"Project root: {ROOT}")
    lines.append(f"Python: {sys.version.split()[0]}")

    if sys.version_info < (3, 10):
        lines.append("FAIL: Python 3.10 or newer is required.")
        return False, "\n".join(lines)

    missing_files = [name for name in ("yuki.py", "main.py", "config.yaml", "requirements.txt") if not (ROOT / name).exists()]
    if missing_files:
        lines.append("FAIL: Missing core project files: " + ", ".join(missing_files))
        return False, "\n".join(lines)

    lines.append("\n--- Subsystem Readiness ---")
    failed_subsystems = []
    for subsystem in CORE_SUBSYSTEMS:
        try:
            importlib.import_module(subsystem)
            lines.append(f"OK: {subsystem}")
        except Exception as exc:
            failed_subsystems.append((subsystem, str(exc)))
            lines.append(f"FAIL: {subsystem} ({exc})")

    lines.append("\n--- Optional Integrations Status ---")
    for label, pkg in OPTIONAL_LIBRARIES.items():
        try:
            importlib.import_module(pkg)
            lines.append(f"AVAILABLE: {label}")
        except Exception:
            lines.append(f"UNAVAILABLE (optional): {label}")

    if failed_subsystems:
        lines.append(f"\nFAIL: {len(failed_subsystems)} core subsystem(s) failed to load.")
        return False, "\n".join(lines)

    lines.append("\nPASS: All core Yuki subsystems and project structures are ready.")
    return True, "\n".join(lines)


def main():
    ok, report = run_diagnostics()
    print(report)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
