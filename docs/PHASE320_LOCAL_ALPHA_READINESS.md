# Phase 320 — Local alpha readiness and startup

## Objective

Shift delivery from infrastructure-only milestones toward a repeatable,
honest local-alpha path. This phase adds a read-only readiness command and
makes the supported startup sequence discoverable in the repository README.

## Readiness command

Run:

```bash
python -m pip install -r requirements.txt
python scripts/local_alpha_check.py
```

The command checks the Python baseline, core dependency presence, configured
provider prerequisites, and workspace writability. For Ollama it checks the
CLI, configured model name, and whether the local server reports that model as
installed. For OpenAI it checks only whether the API key environment variable
is present. For Gemini it checks whether the CLI exists.

The command is read-only: it does not install packages, pull models, edit
configuration, contact cloud APIs, or change provider selection. A pass means
the environment is ready to attempt the local smoke test; it is not evidence
that a real model conversation has succeeded.

## Verification layers

1. `scripts/local_alpha_check.py`: environment prerequisites.
2. `scripts/local_smoke.py`: deterministic coordinator/task/worker wiring with
   a fake provider; no model or network required.
3. `python yuki.py`: interactive CLI with the configured real provider.
4. `python -m pip install -r requirements-web.txt` followed by
   `python scripts/run_web.py`: optional authenticated loopback browser chat.
   The web profile intentionally denies all tools by default.

## Safety boundaries

- Keep browser binding on loopback. Do not expose the current prototype as a
  public multi-user service.
- Never put provider credentials in YAML or source control.
- The fake-provider smoke test is not a real inference test.
- Provider credential presence is not proof that credentials are valid,
  billing is enabled, or upstream service is reachable.
- The browser chat has no authorized tools in its initial profile.
- Production-readiness blockers remain tracked in
  `docs/PRODUCTION_READINESS_CURRENT.md`.

## Verification

```bash
python -m unittest tests.test_local_alpha_check -v
python scripts/local_alpha_check.py
python scripts/local_smoke.py
```

The unit tests exercise the readiness command's OpenAI prerequisite behavior.
The environment and smoke commands must be run on the target developer machine
to establish its actual readiness. No such local-machine execution is claimed
by this commit.
