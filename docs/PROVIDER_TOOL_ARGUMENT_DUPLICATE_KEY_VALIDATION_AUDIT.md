# Provider Tool-Argument Duplicate-Key Validation Audit

**Reviewed revision:** `7b9dd6a8657baf7d35867e8319795900d1640441`  
**Scope:** OpenAI-compatible and Ollama provider response parsing, including non-streaming and streaming structured tool calls.  
**Disposition:** Confirmed validation-consistency gap; no demonstrated authorization bypass.  
**Suggested severity:** P3

## Finding

Provider adapters decode upstream JSON with the standard `json.loads()` behavior before passing normalized tool-call envelopes to `core.structured_tool_protocol.parse_tool_calls()`. Python's default JSON decoder accepts duplicate object keys and silently retains the last value. Once decoded into a dictionary, the original duplicate-key evidence is lost.

The canonical structured tool protocol explicitly rejects duplicate keys at every object depth through `object_pairs_hook=_unique_object` (in `core/structured_tool_protocol.py`). However, the provider adapters invoke that validator only after the provider payload and tool arguments have already been converted to dictionaries and serialized again. The canonical duplicate-key check therefore cannot detect duplicates originating in the upstream response.

### Affected paths

- `providers/openai_compatible.py`
  - `generate_chat()`: parses each provider `function.arguments` string with default `json.loads()` (around line 196).
  - `generate_chat_stream()`: parses SSE event JSON with default `json.loads()` (around line 297), then parses assembled tool arguments with default `json.loads()` (around line 354).
  - `generate_with_tools()`: parses each provider `function.arguments` string with default `json.loads()` (around line 462).
- `providers/ollama.py`
  - `generate_chat()`: parses the complete response JSON with default `json.loads()` (around line 138).
  - `generate_chat_stream()`: parses each NDJSON event with default `json.loads()` (around line 244).

In the Ollama paths, duplicate keys inside nested tool argument objects are collapsed while parsing the enclosing response/event. In the OpenAI-compatible paths, duplicate keys are collapsed either while parsing the enclosing SSE event or while parsing the JSON-encoded `function.arguments` string.

### Impact

A malformed or ambiguous upstream tool-call payload can pass the provider adapter's normalization step even though the provider-neutral protocol is designed to reject duplicate keys. The resulting tool receives the last value retained by Python's decoder.

The current flow still serializes the normalized dictionary and validates it with `parse_tool_calls()`; that preserves size, identifier, call-count, and other protocol checks. This review did not establish a direct authorization bypass or a way to execute an unregistered tool. The impact is therefore a defense-in-depth and protocol-consistency gap, not a demonstrated exploit.

## Recommended remediation

1. Apply duplicate-key rejection at the first JSON decoding boundary for every upstream provider response and stream event.
2. Apply the same strict decoder to OpenAI-compatible `function.arguments` strings before converting them to dictionaries.
3. Prefer a shared, public strict-JSON decoding helper so provider adapters and the canonical tool protocol cannot drift.
4. Add regression tests for duplicate keys at the top level, inside nested tool arguments, and across both streaming and non-streaming provider paths.
5. Verify malformed duplicate-key responses fail closed before any tool dispatch occurs.

## Verification limits

This was a source-level review of the recorded revision. No runtime tests, integration tests, or live provider requests were executed. The finding is based on Python JSON decoder semantics and the inspected call paths; the suggested regression tests remain to be run after remediation.
