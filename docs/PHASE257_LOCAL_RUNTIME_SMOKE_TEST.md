# Phase 257 — Deterministic local runtime smoke check

## Delivered

- Added `scripts/local_smoke.py`, a deterministic coordinator-to-provider wiring check using a fake provider.
- The smoke check submits a specialist plan, dispatches the task, executes one worker cycle, validates persisted workflow budget metadata, and checks durable model-turn/transcript usage.
- By default it uses a temporary workspace. Pass `--workspace PATH` to retain the SQLite database for inspection.
- Added `tests/test_local_smoke.py` and documented both smoke and doctor commands in README.

## Run locally

```bash
python scripts/local_smoke.py
python -m unittest tests.test_local_smoke
python scripts/doctor.py
```

The smoke command requires project dependencies to be installed. It does not require API credentials, Ollama, downloaded models, or external network access. It validates local wiring only and does not establish real inference readiness.

## Explicit limitations

- This environment has repository connector access but no checked-out Yuki working tree or Python execution environment, so the smoke command and tests were not executed here.
- The existing `scripts/doctor.py` validates legacy module imports and project files; it is not a comprehensive platform doctor.
- Real provider inference, Windows/macOS/Linux installation, duration enforcement, and supervised self-upgrade rollback remain release gates.
