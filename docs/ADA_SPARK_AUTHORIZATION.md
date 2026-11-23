# Ada/SPARK authorization invariant

Yuki includes an isolated Ada/SPARK proof model under
`formal/ada/yuki_authorization`. It formalizes the final decision predicate
used by the host's MCP authorization flow.

Execution is permitted only when all of these host-verified conditions hold:

1. A session is authenticated and its owner matches the connection owner.
2. An exact policy exists and is enabled.
3. Required OAuth scopes are granted.
4. The durable policy revision remains current after the request/approval
   boundary.
5. The request audit and authorization-decision audit have both succeeded.
6. Read-only calls need no write confirmation; write and financial calls need
   fresh explicit owner approval for that invocation.

Every record component defaults to deny. The predicate is pure, has no global
state, and has a postcondition equating its result to the complete conjunction
of authorization requirements. The test executable exercises positive and
negative scenarios; GNATprove is the formal proof gate.

## Integration boundary

This is a proof model, not a second authorization service. Python continues to
authenticate sessions, bind owners, read durable policies, validate scopes,
obtain one-shot owner approval, perform revision fencing and write audit
records. Only trusted host code may map those verified facts into the Ada
record. Model output and remote MCP metadata must never populate these fields.

The model proves the decision predicate, not correctness of Python, OS identity,
OAuth provider behavior, database durability, or the truth of caller-supplied
booleans. Cross-language integration requires a separate reviewed adapter and
equivalence tests before any enforcement role is assigned.

## Verification

CI uses Alire, GNAT and GNATprove at proof level 4. A successful proof must
report no unproved checks. This module is not yet connected to production
authorization and must not be described as runtime enforcement.
