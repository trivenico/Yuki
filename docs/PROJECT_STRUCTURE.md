# Yuki project structure

This is the current, deliberately lightweight structure. The core uses the Python standard library so the first setup does not need a model API key or third-party package installation.

```text
Yuki/
├── .github/workflows/tests.yml
├── companion/
│   ├── __init__.py
│   ├── persona.py                 # Stable identity and assistant guidance
│   └── emotion.py                 # Bounded conversational tone helper
├── core/
│   ├── __init__.py
│   ├── permissions.py             # Fail-closed human approval gate
│   └── agent.py                   # Bounded one-skill guarded agent loop
├── docs/
│   ├── INSTALLATION.md
│   └── PROJECT_STRUCTURE.md
├── providers/
│   ├── __init__.py
│   ├── base.py
│   └── mock.py                    # Offline development provider
├── scripts/doctor.py              # Environment/import diagnostics
├── skills/
│   ├── __init__.py
│   ├── base.py                    # Common skill interface and result
│   ├── time_skill.py              # Read-only local clock
│   ├── system_skill.py            # Read-only runtime details
│   └── file_skill.py              # Workspace-confined listing only
├── tests/
│   ├── test_yuki.py
│   ├── test_yuki_identity.py
│   ├── test_yuki_orchestrator.py
│   ├── test_yuki_workflow.py
│   └── test_companion_skills_agent.py
├── yuki.py                        # Existing terminal entry point
├── yuki_agent.py                  # Provider-neutral model boundary
├── yuki_approvals.py              # Workflow approval records
├── yuki_events.py                 # In-process event bus
├── yuki_identity.py               # Name and voice preference constants
├── yuki_learning.py               # Local outcome/lesson history
├── yuki_orchestrator.py           # Guarded task preparation
├── yuki_planner.py                # Bounded plan objects
├── yuki_providers.py              # Provider registry/factory
├── yuki_security.py               # Tool allow-list and metadata audit
├── yuki_tasks.py                  # Local task list
├── yuki_tools.py                  # Existing basic CLI tools
├── yuki_workflow.py               # Workflow state machine
├── requirements.txt               # No third-party dependencies yet
├── install_windows.bat
├── run_yuki_windows.bat
├── install_unix.sh
├── run_yuki_unix.sh
├── README.md
├── ROADMAP.md
└── SECURITY.md
```

## Safety behavior in the new packages

- The persona and tone helper do not claim to infer real emotions or diagnose users.
- The approval gate denies actions unless a configured callback explicitly returns the boolean `True`.
- Every skill validates its input and returns a structured result.
- The new agent accepts only registered skills and executes at most one skill per request. It has no arbitrary shell tool and no autonomous multi-step execution.
- The file skill only lists entries within its configured workspace. It does not read file contents, follow symlinks, or write/delete files.
- The new core agent is a separate foundation and is not yet wired into the existing `yuki.py` CLI or the mock-provider conversation path.

## Local files created at runtime

Yuki may create task, memory, learning-history, and audit JSON files while it runs. These contain local user data and are ignored by Git. Do not commit them or add secrets to them.

## Setup

- Windows: run `install_windows.bat`; after it succeeds, use `run_yuki_windows.bat`.
- macOS/Linux: run `sh install_unix.sh`; after it succeeds, run `sh run_yuki_unix.sh`.
- Diagnostics: `.venv\\Scripts\\python.exe scripts\\doctor.py` on Windows, or `.venv/bin/python scripts/doctor.py` on macOS/Linux.
- Run tests: `python -m unittest discover -s tests -v` using the intended Python environment.

A successful setup does not mean that wake-word detection, speech, a live model provider, autonomous coding, or a cybersecurity lab runner has been implemented.
