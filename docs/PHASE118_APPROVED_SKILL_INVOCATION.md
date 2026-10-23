# Phase 118 — Approval-gated runtime skill invocation

Adds two terminal commands backed by the built-in `SkillRegistry`:

```text
/skills approve NAME
/skills run NAME [ARGUMENT]
```

## Safety model

- Only skills already returned by the built-in registry can be invoked.
- Invocation calls the skill's bounded `execute()` method, not `run()` directly.
- Skills requiring approval, plus skills marked high/critical risk, require an explicit one-shot approval.
- One-shot approvals live only in process memory and are consumed before invocation; restarting Yuki clears them.
- Unknown skills are rejected. Arguments are not included in audit events, and displayed result messages are capped.
- This does not enable dynamic plugin discovery or auto-trust discovered modules.

## Use

First inspect the skill and its impact:

```text
/skills list
/skills inspect NAME
/skills approve NAME
/skills run NAME [ARGUMENT]
```

Only approve skills you understand and intend to run. An approval is not a sandbox: registered skills execute with the Yuki process's OS permissions. Do not expose Yuki to untrusted skills or run high-impact skills in a privileged environment.

## Verification

```bash
python -m unittest tests.test_approved_skill_invocation -v
python -m unittest discover -s tests -v
```
