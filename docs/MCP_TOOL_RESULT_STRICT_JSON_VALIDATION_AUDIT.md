# MCP Tool Result Strict JSON and Structural Validation Audit

## Finding

**Severity: Low-to-medium — non-standard and ambiguous remote result data is accepted**

The MCP client bounds HTTP response bodies to 1 MB, but its JSON decoder uses Python defaults. In particular:

- Duplicate object keys are silently accepted, with the last value winning.
- Non-standard numeric constants such as `NaN`, `Infinity`, and `-Infinity` are accepted by Python's `json.loads()`, even though they are not valid JSON values under RFC 8259.
- Parsed tool results are re-serialized with `json.dumps(result, ensure_ascii=False)` without `allow_nan=False`, so non-finite values can be emitted again as non-standard JSON tokens.

The client also does not validate that a successful `tools/call` result has the expected object shape or bounded content structure before returning it to downstream code.

## Evidence

In `core/mcp_client.py`, `_rpc()` decodes the response with:

```python
result = json.loads(body.decode("utf-8"))
```

No `object_pairs_hook` rejects duplicate keys, and no `parse_constant` rejects non-finite constants.

In `call_tool()`, the returned result is serialized with:

```python
serialized = json.dumps(result, ensure_ascii=False)
scan = scan_untrusted_text(serialized)
return {"result": result, "untrusted": True, "prompt_injection_scan": scan}
```

The default `allow_nan=True` behavior means non-finite values are not rejected during this second serialization either.

The transport does enforce a 1 MB maximum response body. This limits raw response size but does not make ambiguous or non-standard JSON valid.

## Impact

- Duplicate keys can create parser-dependent interpretations of remote tool output. Different consumers may select different values for the same logical field.
- Non-finite values can cross the MCP boundary as Python floats and then be emitted as non-standard JSON text.
- Downstream code expecting standards-compliant JSON may reject, reinterpret, or inconsistently serialize the result.
- Since the client returns raw result data alongside a heuristic prompt-injection scan, consumers must not mistake a clean scan or successful decoding for structural validity or trust.

This is a protocol-integrity and interoperability issue. No direct authorization bypass or memory-exhaustion exploit is established by the inspected source. The existing 1 MB transport limit is a meaningful mitigation against unbounded response bodies.

## Recommended remediation

1. Decode MCP JSON with a duplicate-key rejecting `object_pairs_hook` applied recursively.
2. Supply a `parse_constant` callback that rejects `NaN`, `Infinity`, and `-Infinity`.
3. Catch `RecursionError` from deeply nested JSON and convert it into a stable protocol error; consider an explicit nesting-depth budget if the decoder is replaced or wrapped.
4. Re-serialize with `allow_nan=False` and treat serialization failure as a protocol-invalid response with unknown tool outcome.
5. Validate the MCP tool result envelope before classifying execution:
   - require an object result;
   - validate `content` as a bounded list of supported content blocks;
   - validate optional `structuredContent` as bounded JSON;
   - require `isError`, when present, to be a boolean;
   - reject unknown or malformed content-block shapes according to the supported MCP protocol version.
6. Preserve the existing 1 MB transport response cap and add explicit limits for result depth, collection sizes, and text content before passing data to downstream model/context layers.
7. Add tests for nested duplicate keys, NaN/Infinity constants, deeply nested JSON, malformed result shapes, non-boolean `isError`, oversized content arrays, and strict re-serialization.

## Scope and verification limits

Reviewed:

- `core/mcp_client.py`
- `core/mcp_pinned_transport.py`
- `core/mcp_tool_discovery.py`
- `core/prompt_guard.py`
- `core/structured_tool_protocol.py`

The pinned transport limits response bodies to 1 MB. The strict schema bounds in `mcp_tool_discovery.py` apply to discovered tool input schemas, not to tool-call result data. No runtime tests were executed during this source review, and no live provider behavior is asserted.
