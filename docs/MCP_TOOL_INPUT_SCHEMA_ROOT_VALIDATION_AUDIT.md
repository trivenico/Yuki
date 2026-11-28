# MCP Tool Input Schema Root Validation Audit

## Finding

**Severity: Low — malformed remote tool schemas are published as discovered metadata**

The discovery boundary recursively bounds JSON metadata and freezes the resulting schema, but it does not enforce the MCP tool contract that `inputSchema` is a JSON object. It also substitutes an empty object when the field is absent. Consequently, a remote server can return a scalar, array, or null schema (or omit the required field), and discovery will publish that malformed value in the owner-facing tools response.

This is a metadata/protocol-validation defect. The runtime keeps discovered tools inert and does not use remote schemas as authorization policy; this review does not establish an authorization bypass.

## Evidence

In `core/mcp_tool_discovery.py`:

1. Discovery obtains each schema using `item.get("input_schema", {})`, silently substituting an empty object when the field is missing.
2. `_bounded_json()` intentionally accepts all JSON scalar types, lists, and dictionaries. That is appropriate for recursively validating arbitrary JSON values, but it does not validate the root schema type.
3. The result is frozen and stored directly in `DiscoveredMCPTool.input_schema` without checking that the original field was present and that its root is a dictionary.
4. `api/mcp_management.py` serializes the value directly into the public authenticated discovery response as `input_schema`.

The inspected tests cover size/depth limits, immutable nested metadata, non-finite numbers, invalid/duplicate names, and inert execution flags. They do not cover a missing, null, scalar, or array `inputSchema`.

## Impact and boundaries

Consumers of the discovery endpoint can receive metadata that violates the MCP tool input-schema contract. A UI, schema renderer, model-tool adapter, or downstream validator expecting an object may reject the response, mis-handle it, or silently fall back to an unintended schema.

The impact is limited by existing controls:
- Discovered metadata is explicitly labeled untrusted.
- Discovery does not grant execution permission.
- Tool execution policy is separately host-configured and owner/account scoped.
- Schema values are recursively bounded by depth, item count, string length, and encoded byte size.

No execution authorization bypass or arbitrary code execution is established.

## Recommended remediation

1. Require the `inputSchema` field to be present on every advertised tool.
2. Require its root value to be a JSON object before calling `_bounded_json()`; retain the existing recursive bounds and immutable snapshot behavior.
3. Reject the malformed tool or fail the complete discovery snapshot consistently. Prefer failing the snapshot so clients do not make policy decisions from a partial or malformed tool inventory.
4. Add tests for missing, null, string, number, boolean, and array roots, plus an empty object and a valid nested object.
5. Keep remote schema metadata separate from host-reviewed risk classification, scope requirements, allowlists, and owner approvals.

## Scope and verification limits

Reviewed at PR #1346 head `1ad4037c087e34edd91b99c0056dee02c8a93405`:

- `core/mcp_tool_discovery.py`
- `core/mcp_client.py`
- `core/mcp_runtime.py`
- `api/mcp_management.py`
- `core/mcp_tool_policy.py`
- `tests/test_mcp_tool_discovery.py`
- `tests/test_mcp_runtime.py`
- `tests/test_mcp_runtime_wiring.py`

Source inspection only. No runtime tests, external MCP server interaction, or downstream model/UI schema-consumer behavior was exercised.

## Related existing audits

- `docs/MCP_TOOL_RESULT_STRICT_JSON_VALIDATION_AUDIT.md` covers malformed JSON and result-envelope validation for tool-call responses.
- `docs/MCP_POLICY_REVOCATION_TO_DISPATCH_RACE_AUDIT.md` covers policy revision fencing between approval and dispatch.

This report addresses the distinct root-shape validation of server-advertised tool input schemas.
