# Yuki project structure

This is the current, deliberately lightweight structure. The core uses the Python standard library so the first setup does not need a model API key or third-party package installation.

```text
Yuki/
├── .github/
│   └── workflows/
│       └── tests.yml              # GitHub Actions regression tests
├── docs/
│   └── PROJECT_STRUCTURE.md       # This guide
├── providers/
│   ├── __init__.py
│   ├── base.py                    # Provider interface
│   └── mock.py                    # Offline development provider
├── scripts/
│   └── doctor.py                  # Environment/import diagnostics
├── tests/
│   ├── test_yuki.py
│   ├── test_yuki_identity.py
│   ├── test_yuki_orchestrator.py
│   └── test_yuki_workflow.py
├── yuki.py                        # Current terminal entry point
├── yuki_agent.py                  # Provider-neutral agent boundary
├── yuki_approvals.py              # Human approval records
├── yuki_events.py                 # In-process event bus
├── yuki_identity.py               # Name, wake-word preference, greeting
├── yuki_learning.py               # Local outcome/lesson history
├── yuki_orchestrator.py           # Guarded task preparation
├── yuki_planner.py                # Bounded plan objects
├── yuki_providers.py              # Provider registry/factory
├── yuki_security.py               # Allow-list and metadata audit
├── yuki_tasks.py                  # Local task list
├── yuki_tools.py                  # Small explicitly defined tools
├── yuki_workflow.py               # Guarded workflow state machine
├── requirements.txt               # Third-party dependencies (currently none)
├── install_windows.bat            # Windows setup and test runner
├── run_yuki_windows.bat            # Windows launcher
├── install_unix.sh                # macOS/Linux setup and test runner
├── run_yuki_unix.sh                # macOS/Linux launcher
├── README.md
├── ROADMAP.md
└── SECURITY.md
```

## Local files created at runtime

Yuki may create task, memory, learning-history, and audit JSON files while it runs. These contain local user data and are ignored by Git. Do not commit them or add secrets to them.

## Setup

- Windows: double-click `install_windows.bat`; after it succeeds, use `run_yuki_windows.bat`.
- macOS/Linux: run `sh install_unix.sh`; after it succeeds, run `sh run_yuki_unix.sh`.
- Diagnostics after setup: `.venv\\Scripts\\python.exe scripts\\doctor.py` on Windows, or `.venv/bin/python scripts/doctor.py` on macOS/Linux.
- Run tests directly: `python -m unittest discover -s tests -v` using the intended Python environment.

The setup script checks Python, creates an isolated virtual environment, installs the declared requirements, and runs tests. A successful setup does not mean that wake-word detection, speech, a live model provider, autonomous coding, or a cybersecurity lab runner has been implemented.
