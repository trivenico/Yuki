# Phase 194 — trusted MCP tool policy registry

## Delivered

- Add an immutable, host-authored policy snapshot keyed by exact provider, connected account and tool name, scoped to one authenticated owner.
- Require an explicit risk enum: `read_only`, `write`, or `financial`. Remote MCP descriptions and schemas never classify risk.
- Derive the MCP client's execution allow-list only from enabled policies for the exact provider/account.
- Enforce required OAuth scopes before authorizing a call.
- Permit read-only calls only when an exact enabled policy exists.
- Require a fresh owner approval returning literal `True` for every write or financial call.
- Bind each approver to a live registry revision; stale snapshots and revision lookup failures deny calls.
- Require audit writes for authorization requests and decisions; audit failures deny calls.
- Bound JSON arguments and provide deeply immutable arguments to the owner approval callback.

## Runtime integration

`OAuthMCPConnectionManager.create_policy_client` derives provider identity from its own trusted configuration, accepts owner identity from authenticated host state, binds the connected account, and creates the restricted MCP client with the policy-derived allow-list and per-call approver.

The host supplies:
- A trusted policy snapshot assembled from host-reviewed configuration.
- Granted scopes from the validated OAuth connection.
- An owner approval callback that displays the exact tool and immutable arguments.
- An audit callback that durably records authorization decisions.
- A current-revision resolver backed by the authoritative policy store.

## Security boundaries and limitations

- Unknown, disabled, cross-owner, cross-provider, cross-account and insufficient-scope tools are denied.
- Writes and financial actions are never silently approved. A callback exception, truthy non-boolean response, audit failure or stale policy revision denies the call.
- The revision resolver is a required host integration point; this change does not add durable policy storage or a management UI.
- The audit callback records authorization decisions, not proof that the remote server completed the requested operation. Execution-result auditing and reconciliation remain separate work.
- No MCP provider is connected by this change. No proposals, payments or other platform actions are enabled by default.

## Validation

Regression tests cover exact identity binding, allow-list derivation, risk classification, owner confirmation, stale revision fencing, audit failure, immutable arguments, disabled policies and malformed inputs. CI is authoritative; no local test execution is claimed.
