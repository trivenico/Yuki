# Yuki Security Architecture & Approval Flow

Yuki employs a secure-by-construction architecture centered around human-in-the-loop approval.

## Approval Sequence

```text
User Input -> Command REPL -> Task/Skill Proposal -> PermissionGate -> User Approval -> Execution
                                                           |
                                                    (Denied / Timeout) -> Blocked
```

## Security Rules

1. **Fail-Closed Permissions**: If no approver callback is registered, every sensitive action is denied.
2. **Environment Variable Scrubbing**: Credentials containing `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `CREDENTIAL`, or `AUTH` are scrubbed before launching subprocesses or plugins.
3. **Audit Logging**: Approval decisions are recorded in append-only JSONL format in `workspace/approval_audit.jsonl`.
4. **Code Scanning**: Plugins are scanned for dangerous execution patterns (`os.system`, `eval`, `exec`) before import.
