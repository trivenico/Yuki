# Docker diagnostics JSON parser resource-boundary audit

**Review date:** 2026-11-28  
**Branch:** `research/reference-projects-integration-assessment`

## Finding: deeply nested JSON can escape the parser's error contract

`core/sandbox_runtime.py::parse_docker_info` caps the encoded input at 256 KiB and catches `ValueError` and `UnicodeError` from `json.loads`. It does not catch `RecursionError`.

Python's JSON decoder can raise `RecursionError` for sufficiently deeply nested JSON. Such a payload can therefore escape as an unexpected interpreter exception instead of the parser's documented `SandboxRuntimeError`. The byte-size cap does not impose a nesting-depth limit: a relatively small input can still be deeply nested.

The parser currently checks the top-level object and selected field shapes after decoding, but those checks cannot protect the decoder from excessive nesting before it returns.

## Impact and scope

A malformed or adversarial Docker-info payload can cause diagnostic parsing to fail outside its typed error contract. Depending on the caller, this may abort a diagnostic operation or bypass expected error handling. This finding does not establish that the parser is called on attacker-controlled network input or that the execution runner uses this parser to authorize launches.

## Required remediation

1. Catch `RecursionError` from JSON decoding and translate it into `SandboxRuntimeError`.
2. Prefer an explicit bounded-depth parser or a pre-validation strategy with a well-defined maximum nesting depth.
3. Keep the byte-size cap and validate expected top-level fields and shapes.
4. Add a small, deeply nested JSON test that asserts deterministic typed rejection without an uncaught recursion exception.

No runtime code changed, no Docker daemon was contacted, no container was created or removed, and no local tests were run.
