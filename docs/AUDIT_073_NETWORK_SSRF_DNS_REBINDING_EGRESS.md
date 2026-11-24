# Audit 073 — Network Security, SSRF, DNS Rebinding, Egress Controls & External Service Trust

**Scope:** outbound HTTP(S), provider endpoints, OAuth metadata/token/revocation, MCP transport, web fetch, DNS resolution, redirects, TLS identity, and private-network access.

**Reviewed source on main:** core/mcp_pinned_transport.py, core/oauth_http_transport.py, core/mcp_oauth_discovery.py, core/mcp_oauth_metadata.py, core/mcp_oauth_client_registration.py, core/mcp_oauth_provider_bootstrap.py, core/mcp_client.py, core/mcp_runtime.py, core/oauth_authorization_service.py, core/oauth_provider_identity.py, providers/openai_compatible.py, providers/ollama.py, skills/web_skill.py, skills/web_deep_read.py, plus related transport/discovery tests and repository tree.

## Positive controls observed

- OAuth and MCP each have dedicated HTTPS transports that require exact hostname allow-lists, reject URL credentials and non-default ports, resolve hostnames, reject non-global addresses, pin the selected public IP to the TCP connection, and retain the original hostname for TLS SNI/certificate verification.
- Those transports reject redirects, bound request/response sizes, use finite timeouts, validate header names/values, and return sanitized transport errors.
- MCP transport validates the endpoint host against an explicit allow-list and rejects DNS answers containing non-global addresses before connecting to the selected pinned address.
- OAuth metadata discovery only fetches through an injected trusted transport and checks advertised metadata hosts against an allow-list. Metadata parsing validates expected resource/issuer relationships and supported endpoint forms.
- OpenAI-compatible provider endpoints require HTTPS, reject URL credentials/query/fragment and non-global literal IPs, and restrict hostnames to built-in or explicit exact-host allow-lists. Redirects are disabled before sending the API key.
- Ollama requires HTTPS for non-loopback hosts, allows HTTP only for loopback, and disables redirects.
- Web fetch requires HTTPS, an exact configured host allow-list, rejects localhost and common local suffixes, checks resolved addresses, disables redirects, bounds response bytes, and only accepts text content types.

## Findings

### P1 — WebSkill DNS validation is vulnerable to a DNS rebinding / time-of-check-to-time-of-use gap (fixed in companion PR)

Before the fix, WebSkill called getaddrinfo and verified that every returned address was public, then passed the original hostname to urllib. urllib performed a separate DNS resolution when opening the connection. DNS could change between validation and connection, so the actual TCP destination was not guaranteed to be one of the addresses that had been checked.

The exact-host allow-list materially reduces exposure but does not eliminate this gap when an allow-listed hostname's DNS answer changes or is compromised.

**Fix submitted:** replace the independent urllib resolution with an HTTPS connection that pins TCP to one validated public IP while using the original hostname for TLS SNI and certificate verification. Every DNS answer is validated; mixed public/private answer sets are rejected. Redirects remain rejected, response size/content-type limits remain, and malformed ports are normalized to a safe WebSkillError. Added regression tests.

### P2 — Outbound policy is distributed across transports and providers

MCP, OAuth, WebSkill, and provider adapters implement similar but separate URL/DNS/redirect rules. The controls are not expressed as one shared egress policy or centrally observable allow/deny decision.

**Impact:** future integrations can accidentally bypass one of the hardened transports or diverge in treatment of IP literals, IPv4-mapped IPv6, DNS answer sets, redirects, ports, or timeout/response bounds.

**Recommendation:** define a shared outbound destination policy and transport contract with explicit profiles (public SaaS, local loopback, approved private network). Keep profile exceptions explicit; do not globally block private destinations where a feature intentionally talks to a user-approved local device. Add common conformance tests and egress audit events.

### P2 — Network-layer egress enforcement is not demonstrated by application source

Application allow-lists do not constrain arbitrary code, compromised dependencies, shell tools, or future network clients running in the same process/container. No deployment firewall, Kubernetes NetworkPolicy, cloud security-group egress policy, or metadata-service block was established by the reviewed source.

**Recommendation:** enforce defense-in-depth at the host/container/network layer. Deny cloud metadata IPs and private/control-plane ranges by default for workloads that do not need them; route approved local-device access through a narrow broker; monitor DNS and outbound connection destinations.

### P2 — Ollama HTTPS endpoint trust is configurable but not centrally governed

Ollama permits an administrator-configured HTTPS endpoint beyond loopback and does not itself enforce an exact host allow-list or public/private destination policy. This may be intentional for self-hosted deployments, but configuration provenance and egress policy must be trusted.

**Recommendation:** document that non-loopback Ollama endpoints are privileged configuration, validate endpoint ownership/allow-list at setup time, and apply network egress controls. Avoid blocking intentional private-network deployments without an explicit endpoint profile.

## Remediation status

- **Implemented in companion PR #1133:** WebSkill DNS pinning, address validation and regression tests.
- No blanket private-network block was added to Ollama because local/private HTTPS endpoints can be a legitimate self-hosted deployment choice.
- No shared egress-policy refactor was attempted in this audit; it spans multiple security-sensitive transports and requires compatibility tests for approved local/private integrations.

## Validation status

- Source inspection completed for the listed transport/provider/discovery paths.
- Regression tests added but **not executed**.
- No live DNS rebinding simulation, packet capture, proxy, container firewall, cloud metadata service, or deployment egress policy was available/performed.
- PRs remain open and unmerged; no deployment claim is made.
