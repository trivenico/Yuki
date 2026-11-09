"""Opt-in end-to-end real-provider acceptance through Yuki's browser ASGI runtime.

The check creates an isolated temporary workspace, bootstraps a disposable
administrator, authenticates, and submits one fixed conversation over the
same ASGI route used by the browser. It never logs prompts, responses,
credentials, environment values, or raw exception messages.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROMPT = "Reply with exactly: Yuki local provider acceptance passed."
EXPECTED_RESPONSE = "Yuki local provider acceptance passed."
MAX_RESPONSE_CHARS = 4_000
SUPPORTED_PROVIDERS = {"openai", "ollama"}


async def _asgi_request(runtime, method, path, payload=None, *, token):
    import asyncio
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    sent = []
    consumed = False

    async def receive():
        nonlocal consumed
        if consumed:
            return {"type": "http.disconnect"}
        consumed = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    await runtime.web({
        "type": "http", "method": method, "path": path,
        "headers": [
            (b"host", b"localhost"),
            (b"authorization", ("Bearer " + token).encode("ascii")),
            (b"content-type", b"application/json"),
        ],
        "query_string": b"",
    }, receive, send)
    if not sent or sent[0].get("type") != "http.response.start":
        raise RuntimeError("Browser runtime returned an invalid HTTP response.")
    status = sent[0]["status"]
    raw = sent[1].get("body", b"") if len(sent) > 1 else b""
    try:
        payload = json.loads(raw) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Browser runtime returned invalid JSON.") from exc
    return status, payload


def _build_runtime(config, provider):
    import scripts.run_web as run_web
    # Keep the production composition root and provider adapter/manager; inject
    # only the already-validated provider and isolated workspace configuration.
    previous_loader = run_web.load_config
    previous_factory = run_web.create_provider
    try:
        run_web.load_config = lambda: dict(config)
        run_web.create_provider = lambda _config: provider
        return run_web.create_app()
    finally:
        run_web.load_config = previous_loader
        run_web.create_provider = previous_factory


def run_acceptance(*, config_loader=None, provider_factory=None, environ=None, runtime_factory=None):
    env = os.environ if environ is None else environ
    if env.get("YUKI_RUN_REAL_PROVIDER_ACCEPTANCE") != "1":
        return {
            "result": "skipped",
            "reason": "Set YUKI_RUN_REAL_PROVIDER_ACCEPTANCE=1 to authorize one real provider request.",
        }

    if config_loader is None:
        from core.config import load_config
        config_loader = load_config
    if provider_factory is None:
        from providers.factory import create_provider
        provider_factory = create_provider

    config = dict(config_loader())
    provider_name = str(config.get("provider", "")).strip().lower()
    if provider_name not in SUPPORTED_PROVIDERS:
        raise RuntimeError("Configured provider is not supported for real acceptance testing.")

    provider = provider_factory(config)
    if provider.is_available() is not True:
        raise RuntimeError("Configured provider is not available; no request was sent.")

    # Isolate auth, conversations, memory, and provider-selection state from the
    # user's normal workspace. Provider credentials remain in the process env.
    with tempfile.TemporaryDirectory(prefix="yuki-real-provider-") as workspace:
        config["workspace"] = workspace
        username = "acceptance-" + secrets.token_hex(6)
        password = secrets.token_urlsafe(24)
        previous = {
            key: os.environ.get(key)
            for key in ("YUKI_WORKSPACE", "YUKI_BOOTSTRAP_ADMIN_USERNAME", "YUKI_BOOTSTRAP_ADMIN_PASSWORD")
        }
        try:
            os.environ["YUKI_WORKSPACE"] = workspace
            os.environ["YUKI_BOOTSTRAP_ADMIN_USERNAME"] = username
            os.environ["YUKI_BOOTSTRAP_ADMIN_PASSWORD"] = password
            factory = runtime_factory or _build_runtime
            runtime = factory(config, provider)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        try:
            session = runtime.app.auth.authenticate(username, password)
            status, conversation = __import__("asyncio").run(_asgi_request(
                runtime, "POST", "/v1/conversations",
                {"title": "Provider acceptance"}, token=session.token,
            ))
            if status != 201 or not isinstance(conversation.get("conversation_id"), str):
                raise RuntimeError("Authenticated conversation creation failed.")

            status, reply = __import__("asyncio").run(_asgi_request(
                runtime, "POST",
                "/v1/conversations/" + conversation["conversation_id"] + "/messages",
                {"content": PROMPT, "request_id": "real-provider-acceptance", "expected_version": 0},
                token=session.token,
            ))
            response = reply.get("answer")
            if status != 200 or reply.get("status") != "completed":
                raise RuntimeError("Authenticated browser conversation did not complete.")
            if not isinstance(response, str) or not response.strip():
                raise RuntimeError("Provider returned an empty or invalid response.")
            if len(response) > MAX_RESPONSE_CHARS:
                raise RuntimeError("Provider response exceeded the acceptance-test size limit.")
            if response.strip() != EXPECTED_RESPONSE:
                raise RuntimeError("Provider did not satisfy the exact-response acceptance contract.")

            model = getattr(provider, "model", None)
            if not isinstance(model, str) or not model:
                model = str(config.get("ollama_model") or config.get("openai_model") or provider_name)
            return {
                "result": "passed",
                "provider": provider_name,
                "model": model[:120],
                "transport": "authenticated_browser_asgi",
                "response_characters": len(response),
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "credentials_or_content_logged": False,
            }
        finally:
            runtime.close()


def main() -> int:
    try:
        result = run_acceptance()
    except Exception as exc:
        print(json.dumps({
            "result": "failed",
            "error_type": type(exc).__name__,
            "details": "Inspect local provider configuration and provider logs; sensitive exception text is suppressed.",
        }, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["result"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
