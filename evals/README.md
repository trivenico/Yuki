# Yuki evaluation harness

These deterministic evaluations are separate from the normal unit-test suite and use only the Python standard library. They do not contact a live model, run shell commands, or execute arbitrary code.

## Run

```bash
python evals/run_evals.py --fast
python evals/run_evals.py
```

The fast subset is intended for pull requests; the full set is intended for nightly/manual runs. Provider tests patch the HTTP transport with recorded fixtures.

## Add a case

1. Add a JSON file in `evals/cases/` with a stable `id`, description, and explicit assertions.
2. Add a deterministic unittest in `evals/test_scenarios.py` that executes the behavior and asserts status, step count, and side effects.
3. Add safety-critical fast tests to the allow-list in `run_evals.py`.
4. Include hostile content only as inert fixture data; never execute it.
5. Keep evals isolated from `tests/` so developers can run fast unit tests separately.

## Current limitations

The repository ref used for this phase does not contain `companion/chat.py` / `ChatSession`. The chat smoke evaluation therefore verifies only that text returned by `YukiAgent.ask` is not dispatched as a tool. It does not yet prove 12-turn history trimming or memory-context injection. Phase 10 cannot be considered fully accepted until the intended ChatSession is present and those assertions are added.
