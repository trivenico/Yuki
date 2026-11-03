# REA and Colibri integrations

Audit date: 2026-10-31.

## REA — authorized software investigation

- Upstream: https://github.com/morluto/rea
- License: MIT for REA-authored code; third-party tools and assets retain their own terms.
- Yuki integration: `skills/upstream/rea/rea-investigation/SKILL.md` is a data-only workflow guide that can be imported using Yuki's existing `/skill install` command.
- Scope: evidence-based investigation of authorized applications, binaries, websites, model artifacts and runtime behavior. Static inspection is preferred; runtime interaction requires separate approval.
- Limitation: REA exposes a local MCP server. Yuki's current MCP client supports HTTPS Streamable HTTP only and does not launch stdio MCP processes. The skill therefore documents external MCP-host setup and does not falsely advertise a native connection.
- Setup: follow the official REA instructions at https://github.com/morluto/rea. Its documented setup requires a supported Node.js 22/24/26 release and npm. The REA repository is MIT-licensed; the complete upstream license text is retained at `licenses/MIT-REA.txt`. REA bundles or invokes third-party analysis tools, whose individual licenses and installation requirements remain separate. Do not run `npx rea-agents setup` without explicit user approval.

## Colibri — optional local inference engine

- Upstream: https://github.com/JustVugg/colibri
- License: Apache-2.0 for Colibri-authored code; model weights and third-party components have separate licenses.
- Yuki integration: `providers/colibri.py` implements Yuki's provider contract against Colibri's local OpenAI-compatible `/v1/chat/completions` API.
- Privacy: the adapter accepts only HTTP loopback endpoints (localhost or loopback IP); remote endpoints are rejected. It does not send prompts to a cloud service.
- Runtime: Colibri remains a separately installed optional dependency. Yuki does not build native code, download large model weights, or start/stop its server automatically.
- Model id: configure the exact model id reported by `coli status`.
- Suggested server endpoint: `http://127.0.0.1:8000/v1`.

### Configure

Set environment variables:

```sh
YUKI_PROVIDER=colibri
YUKI_COLIBRI_MODEL=<model-id-from-coli-status>
YUKI_COLIBRI_BASE_URL=http://127.0.0.1:8000/v1
```

Start Colibri separately using its official launcher, then start Yuki. The adapter sends a local placeholder bearer token; it is not an authentication boundary. The adapter blocks redirects and only permits loopback URLs. Keep the service bound to loopback and do not expose it to a LAN or the public internet.

### Hardware expectations

Colibri supports CPU and optional GPU backends, but model fit and speed depend heavily on RAM, disk and model selection. A large MoE model may require tens or hundreds of GB of storage and substantial RAM; "runs on a normal PC" does not mean every model runs well on every PC. Use Colibri's own hardware planner/doctor and model catalog before downloading. Start with a small model that fits available memory; keep Ollama and cloud APIs as alternative providers.

### Safety and limitations

- Provider support currently covers plain text chat only; Yuki does not infer tool-calling, vision, image generation or reasoning capabilities from Colibri's model name.
- Yuki does not silently fall back to another provider when Colibri is unavailable.
- The placeholder API key is not access control. Loopback binding is mandatory.
- Do not redistribute model weights without checking each model's license and use restrictions.
