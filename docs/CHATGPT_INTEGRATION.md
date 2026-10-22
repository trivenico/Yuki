# ChatGPT integration (MCP)

Yuki includes an authenticated MCP-over-HTTP bridge exposing three tools:
- `yuki_status`: reports the selected provider and readiness.
- `yuki_chat`: sends a bounded text prompt to Yuki's configured provider, with optional session context when memory is enabled.
- `yuki_delete_session`: deletes stored turns for a supplied session ID when memory is enabled.

It does not expose shell execution, skills, arbitrary filesystem access, or other state-changing actions.

## 1. Requirements

- Python 3.10+ and the project's normal dependencies.
- A configured Yuki provider. Mock mode is useful for a connectivity smoke test; configure Ollama for local-model replies.
- A long random bearer token stored only in the service environment.
- For direct remote connections, a public HTTPS endpoint with TLS termination, firewall restrictions, and monitoring. Do not expose plain HTTP on a public interface.
- Alternatively, for a private/local instance, use OpenAI's Secure MCP Tunnel where available. Keep the bridge bound to loopback and follow the tunnel's current installation and authentication guidance.

## 2. Configure

Generate a token locally and keep it out of Git:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set it in the environment of the process that runs Yuki:

```sh
# macOS/Linux shell example
export YUKI_MCP_BEARER_TOKEN='paste-a-new-random-token-here'
export YUKI_MCP_HOST=127.0.0.1
export YUKI_MCP_PORT=8765
python -m integrations.chatgpt_mcp
```

PowerShell equivalent:

```powershell
$env:YUKI_MCP_BEARER_TOKEN = "paste-a-new-random-token-here"
$env:YUKI_MCP_HOST = "127.0.0.1"
$env:YUKI_MCP_PORT = "8765"
python -m integrations.chatgpt_mcp
```

The process exits with a configuration error if the token is missing/short. Loopback binding is the default. Binding to a non-loopback interface additionally requires `YUKI_MCP_ALLOW_REMOTE=true` and a longer token; this is not a substitute for HTTPS, a firewall, or a reviewed deployment.

## 3. Smoke test

In another terminal, check the health endpoint:

```sh
python -c "from urllib.request import urlopen; print(urlopen('http://127.0.0.1:8765/health').read().decode())"
```

Check MCP tool discovery using the token:

```sh
python - <<'PY'
import json
from urllib.request import Request, urlopen
token = "paste-the-same-token-here"
payload = {"jsonrpc":"2.0","id":1,"method":"tools/list"}
request = Request(
    "http://127.0.0.1:8765/mcp",
    data=json.dumps(payload).encode(),
    headers={"Content-Type":"application/json","Accept":"application/json, text/event-stream","Authorization":"Bearer "+token},
)
with urlopen(request, timeout=5) as response:
    print(response.read().decode())
PY
```

Run tests with `python -m unittest discover -s tests -v`. The bridge tests cover authentication, tool exposure, prompt bounds, chat admission control, session history limits, and deletion.

## 4. Connect it in ChatGPT

ChatGPT custom MCP availability and authentication options depend on your account/workspace plan and current product rollout. Use ChatGPT on the web.

1. Make the MCP endpoint reachable using a supported public HTTPS endpoint or Secure MCP Tunnel.
2. Open **Plugins** (or the current **Apps** management experience), choose **Add custom MCP server**, and provide a name and the endpoint URL ending in `/mcp`.
3. Configure an authentication method supported by the ChatGPT connection UI and compatible with this bridge's bearer-token authentication. If the UI does not support a static bearer token for your connection, do not disable bridge authentication; implement a supported OAuth flow or use the supported Secure MCP Tunnel path instead.
4. Review the discovered tools. Enable only `yuki_status` and `yuki_chat`.
5. Start a new chat, select the Yuki app for the message, and ask it to check status or answer a simple test prompt.

Official instructions: [Connecting custom MCP servers in ChatGPT](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt).

## 5. Optional conversation memory

Conversation memory is disabled by default. To enable bounded session history, configure the following environment variables before starting the bridge:

```sh
export YUKI_MCP_MEMORY_ENABLED=true
export YUKI_MCP_MEMORY_DB=./yuki_workspace/chat_history.sqlite3
export YUKI_MCP_MEMORY_RETENTION_DAYS=7
```

The retention value must be between 1 and 90 days. The SQLite store retains at most 8 turns per session and 1,000 sessions by default, prunes expired sessions, and offers the `yuki_delete_session` tool. When memory is disabled, the bridge does not create a database and does not retain conversation history.

Pass the same opaque `session_id` on each `yuki_chat` call to reuse a session. Use a random, unguessable ID per user/conversation and never reuse one across different users. This bridge does not yet authenticate a user identity or bind session IDs to ChatGPT accounts; therefore memory should remain disabled for multi-user public deployments until identity-bound session authorization is implemented. SQLite file permissions and encrypted storage/backups remain deployment responsibilities. Deleting a session deletes the rows from the active database, not copies in backups.

## 6. Security and limitations

- Do not paste the bearer token into a prompt or commit it to the repository.
- The bridge has no OAuth authorization server yet. Static bearer authentication is intentionally simple; use only if the ChatGPT connection method you choose supports it. MCP POST requests must send `Accept: application/json, text/event-stream`.
- Conversation persistence is optional and disabled by default. If enabled, session IDs are not yet bound to authenticated user identities, so keep memory disabled for public multi-user deployments.
- Chat messages are passed to Yuki's configured provider. With a cloud-enabled hybrid configuration, the provider's own task allow-list governs routing; keep private messages local unless you deliberately configure and approve disclosure.
- The bridge applies a bounded per-peer request window, limits JSON-RPC batches to 10 items, applies a request read timeout, and admits at most four concurrent chat generations by default. Excess chat requests receive a retryable capacity error; deployments should still configure upstream/provider timeouts. A reverse proxy must additionally enforce HTTPS, connection limits, upstream timeouts, and access logging that does not record authorization headers or message bodies.
- Do not add write tools, shell, file read/write, device controls, or unrestricted skills until each has its own authorization, approval, audit, and tests.
- A passing unit test is not a deployment or security certification. Validate the actual endpoint and account's MCP support before relying on it.
