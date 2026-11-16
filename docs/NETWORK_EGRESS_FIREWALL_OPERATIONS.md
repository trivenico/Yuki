# Network egress firewall operations

Yuki's application-layer network firewall is a host allowlist, not a replacement for host or cloud egress controls.

## Provider transport coverage

- OpenAI-compatible HTTPS requests use the shared firewall instance supplied by the application composition root.
- Remote Ollama HTTPS requests also use that exact shared firewall instance. Its hostname must be present in the shared allowlist; otherwise provider construction fails closed.
- Loopback Ollama HTTP is a separate local-only transport pinned to a loopback IP. It does not use the remote HTTPS firewall path.
- Gemini CLI subprocess networking is not controlled by the Python firewall. When a shared firewall is required, provider construction rejects Gemini CLI until OS/container-level egress isolation is configured.
- MCP and OAuth transports have pinned HTTPS implementations and accept the shared firewall. The API composition root verifies exact instance identity when an MCP runtime is supplied.

## Deployment requirements

1. Enable the firewall only with explicit exact-host allowlists for every required remote endpoint.
2. Keep the host/cloud firewall default-deny as the outer enforcement layer.
3. Do not treat a successful component registration as proof that a subprocess is network-isolated.
4. Review every newly added HTTP client, SDK, OAuth refresh path, and connector before enabling it in a production profile.
5. Keep audit delivery durable and monitored; the default application logger is not a compliance-grade audit store.

Remote Ollama should only be used when its HTTPS hostname is intentionally included in the same shared policy. Local loopback Ollama is restricted to loopback and is not an arbitrary HTTP proxy.
