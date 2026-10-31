# Phase 97 — independent Python change reviewer

## Purpose

Adds a deterministic, read-only second-pass reviewer for proposed Python file contents. It does not execute candidate changes or modify the workspace.

## Checks

- Dynamic \`eval\` / \`exec\`.
- Shell command execution through \`os.system\` / \`os.popen\`.
- \`subprocess\` calls with literal \`shell=True\`.
- Pickle deserialization.
- Unsafe YAML loader use.
- Requests calls with TLS verification disabled.
- Dynamic module import.
- Python syntax errors.
- Common hard-coded credential and private-key patterns.

## Safety properties

- Repository-relative path validation.
- Maximum 100 files, 250 KB per file, and 1 MB total source.
- AST parsing; candidate source is never imported or executed.
- Credential values are redacted from emitted evidence.
- Syntax failures, skipped non-Python files, and high/critical findings prevent a clean approval signal.
- Findings are deterministic and include rule ID, severity, line, bounded evidence, and remediation.

## Limitations

- This is a heuristic static reviewer, not an LLM-based reviewer or a complete security analyzer.
- It has false positives and false negatives; aliases, reflection, indirect calls, native code, generated code, and semantic data flow are not comprehensively analyzed.
- Only Python files are scanned. Any skipped non-Python change makes the report incomplete and not approval-eligible.
- It does not replace type checking, dependency/SCA scanning, tests, sandbox execution, human review, or CI.
- \`approved_for_merge\` means only that this limited rule set found no blocking pattern in a fully scanned Python-only input. It is not authorization to merge or deploy.
- Capability is registered as experimental and is not yet wired into every pull request, CLI, desktop, skill, or agent workflow.

## Verification

    python -m unittest tests.test_independent_code_review -v
    python -m unittest tests.test_capability_manifest -v
    python -m unittest discover -s tests -v

## Coding-task lifecycle integration

`core/coding_task_review.py` binds the static report to the exact stored patch SHA-256 and task version. It accepts the proposed post-change source snapshot from a trusted caller, and does not apply patches or execute code. A clean, fully scanned Python-only report attaches bounded review evidence and advances a draft task only to `ready_for_review`. Blocking or incomplete reports leave the task in draft. A human must still inspect the patch and evidence and explicitly perform the separate approval transition. The evidence explicitly records `merge_authorized: false`.

The integration trusts the caller to supply the exact post-patch source snapshot; it does not independently reconstruct that snapshot from the Git diff. Production integration must obtain this snapshot from a trusted isolated checkout and bind its revision to the patch digest.

Additional verification:

    python -m unittest tests.test_coding_task_review -v
