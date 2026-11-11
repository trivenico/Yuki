# Phase 191 — Upwork official MCP readiness and policy boundary

Date reviewed: 2026-11-11
Recheck required by: 2026-11-18
Status: researched; disabled; no credentials or network adapter configured

## Integration decision

Use only Upwork's official hosted MCP endpoint for the future freelancer workflow:

- MCP endpoint: https://mcp.upwork.com/mcp
- Official setup and capability guide: https://www.upwork.com/ai/mcp
- Official MCP support guide: https://support.upwork.com/hc/en-us/articles/55446516654611-How-to-use-Upwork-with-AI-agents-through-MCP
- Official API/MCP legal terms: https://www.upwork.com/legal

Do not scrape the Upwork website, automate browser interaction to collect jobs, reuse a different account's credentials, or assume the ordinary Upwork GraphQL API is interchangeable with the MCP server.

## OAuth and deployment gate

Upwork MCP uses OAuth 2.1 and dynamic client registration. Upwork states that a custom agent's callback/redirect URL may require individual Support approval and that approval is not guaranteed. Yuki's current static-provider OAuth authorization service is not, by itself, an implementation of Upwork's MCP OAuth 2.1/DCR handshake.

Before enabling the provider:
1. Obtain the exact public HTTPS Yuki callback origin and register/request approval from Upwork Support.
2. Confirm Upwork's approved redirect URI and OAuth/DCR requirements for this deployment.
3. Implement and test the MCP authorization-server metadata and dynamic client registration requirements supported by Upwork.
4. Verify issuer, resource audience, PKCE, state, callback binding, token storage and refresh behavior end to end.
5. Complete a human-controlled connection and inspect the exact tools/scopes returned by Upwork.
6. Keep every tool disabled until the owner reviews the discovered tool inventory and approves a minimal allow-list.

No callback URI has been submitted or approved. No live connection exists.

## Action policy

| Capability | Yuki policy |
|---|---|
| Search and read job opportunities | Read-only after official MCP connection; owner supplies search criteria |
| Summarize and compare returned jobs | Allowed as explanation; preserve Upwork result ordering unless owner asks for a sort |
| Independently rank, score or choose jobs | Prohibited by Upwork MCP guidance; owner chooses |
| Draft proposal | Draft-only; owner reviews |
| Submit proposal | Explicit owner confirmation required; consumes Connects |
| Draft/send messages | Draft first; send only after explicit owner confirmation |
| Offers, contract acceptance, escrow, milestone funding/payment/approval | Complete on Upwork.com; do not execute as autonomous Yuki actions |
| Payout settings, withdrawals, legal acceptance | Disabled; owner handles directly on the platform |

Never represent a proposed amount, contract, or expected earnings as realized revenue. Track actual revenue only from owner-confirmed platform evidence.

## Separate ordinary Upwork API

Upwork's ordinary developer API is a different integration path using OAuth 2.0 and application credentials. Its developer documentation says API-key requests are reviewed and lists account eligibility conditions, including identity verification, a verified payment method, lifetime earnings/spend thresholds and (for freelancers/agencies) a Job Success Score threshold. Upwork's help documentation describes personal/internal use and no third-party sandbox. These requirements are not requirements to use the hosted official MCP server, and the two auth systems must not be conflated.

## Code state

core/platform_catalog.py records an Upwork capability profile with:
- canonical official domains only;
- no inferred region eligibility;
- reviewed policy URL and a short recheck window;
- read, draft, submit, messaging and financial action classifications;
- enabled=False, so every action remains effectively unsupported.

The profile is metadata, not an adapter. It does not initiate requests, expose a Connect button, store credentials, or claim Upwork support is operational. Tests ensure the profile remains disabled and mutating actions remain unsupported.

## Enablement checklist

- [ ] Owner confirms a Yuki public HTTPS deployment origin.
- [ ] Upwork Support approves the exact redirect URI (if required).
- [ ] DCR and OAuth 2.1 integration is implemented for the Upwork MCP server.
- [ ] Owner completes sign-in and reviews tool inventory.
- [ ] Tool schemas and host allow-list are reviewed and pinned.
- [ ] Job-search ordering and owner-choice rules are enforced.
- [ ] Proposal/message approval and financial-action boundaries are verified.
- [ ] End-to-end tests pass; only then consider changing the profile's disabled flag.
