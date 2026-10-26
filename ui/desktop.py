"""PyWebView desktop chat and status bridge.

The bridge exposes only text chat and read-only status. It never dispatches skills,
shell commands, or workspace writes. Cloud chat requires a per-message UI confirmation
and the provider's approval callback must agree before the request is sent.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from yuki_agent import YukiAgent

MAX_DESKTOP_PROMPT_CHARS = 8_000
CLOUD_PROVIDERS = frozenset({
    "openai", "anthropic", "deepseek", "glm", "openai-compatible", "custom",
})


class DesktopBridge:
    def __init__(
        self,
        agent: Any,
        ai: Any,
        config: dict[str, Any],
        *,
        provider_factory: Callable | None = None,
        agent_factory: Callable = YukiAgent,
    ) -> None:
        self.agent = agent
        self.config = dict(config or {})
        self._approval_context = threading.local()
        if provider_factory is None:
            from providers.factory import create_provider
            provider_factory = create_provider
        provider = provider_factory(
            self.config,
            cloud_approver=self._cloud_approval_callback,
        )
        self.ai = agent_factory(provider, usage_tracker=getattr(ai, "usage_tracker", None))
        self.provider_name = str(self.config.get("provider", "mock")).strip().lower()

    def _cloud_approval_callback(self, *_args, **_kwargs) -> bool:
        return getattr(self._approval_context, "allowed", False) is True

    def get_status(self) -> dict[str, Any]:
        try:
            registry = getattr(self.agent, "registry", None)
            names = registry.names() if registry is not None else []
            skills = self.agent.available_skills() if callable(
                getattr(self.agent, "available_skills", None)
            ) else []
            provider_status = self.ai.status() if callable(getattr(self.ai, "status", None)) else "Unknown"
            return {
                "ready": True,
                "provider": self.provider_name,
                "provider_status": str(provider_status)[:160],
                "cloud_confirmation_required": self.provider_name in CLOUD_PROVIDERS,
                "skills": [str(name)[:100] for name in names if isinstance(name, str)][:200],
                "skill_count": min(len(skills), 200) if isinstance(skills, list) else 0,
                "message": (
                    "Chat uses the configured text provider only. Registered skills are shown "
                    "for visibility and are not executed by desktop chat."
                ),
            }
        except Exception:
            return {
                "ready": False, "provider": self.provider_name,
                "provider_status": "Unknown", "cloud_confirmation_required": (
                    self.provider_name in CLOUD_PROVIDERS
                ), "skills": [], "skill_count": 0,
                "message": "Runtime status unavailable.",
            }

    def ask(self, prompt: str, cloud_approved: bool = False) -> dict[str, Any]:
        if not isinstance(prompt, str) or not prompt.strip():
            return {"ok": False, "message": "Enter a message before sending."}
        if len(prompt) > MAX_DESKTOP_PROMPT_CHARS:
            return {"ok": False, "message": f"Messages are limited to {MAX_DESKTOP_PROMPT_CHARS} characters."}
        is_cloud = self.provider_name in CLOUD_PROVIDERS
        if is_cloud and cloud_approved is not True:
            return {
                "ok": False,
                "requires_confirmation": True,
                "message": "No data sent. Confirm the cloud request before sending.",
            }
        if not isinstance(cloud_approved, bool):
            return {"ok": False, "message": "Invalid approval value; no data sent."}
        self._approval_context.allowed = is_cloud and cloud_approved
        try:
            answer = self.ai.ask(prompt.strip())
        except Exception:
            return {"ok": False, "message": "The provider request failed safely. Check provider setup and retry."}
        finally:
            self._approval_context.allowed = False
        if not isinstance(answer, str) or not answer.strip():
            return {"ok": False, "message": "The provider returned an empty response."}
        if answer in {
            "Yuki: The provider encountered an error.",
            "Yuki: The AI provider is unavailable.",
            "Yuki: The provider returned an unsupported response.",
        }:
            return {"ok": False, "message": answer}
        # Avoid reflecting oversized or structured provider output directly into the UI.
        answer = answer.strip()
        if len(answer) > 12_000:
            answer = answer[:12_000] + "\n[Response truncated by Yuki.]"
        return {"ok": True, "message": answer}


def run_desktop(agent, *, ai=None, config=None) -> None:
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError(
            "Install the optional desktop dependency with: pip install -r requirements-desktop.txt"
        ) from exc
    if ai is None or not isinstance(config, dict):
        raise RuntimeError("Desktop chat requires the configured provider runtime.")
    try:
        bridge = DesktopBridge(agent, ai, config)
    except Exception as exc:
        # Provider construction errors can contain configuration details; expose only type.
        raise RuntimeError(
            f"Desktop provider initialization failed ({type(exc).__name__}). Check configuration."
        ) from None
    html = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Yuki</title><style>
:root{color-scheme:dark;font:15px system-ui,sans-serif;background:#10131a;color:#edf2fa}
*{box-sizing:border-box}body{margin:0;min-height:100vh}main{max-width:1100px;margin:auto;padding:28px;display:grid;grid-template-columns:250px 1fr;gap:22px;min-height:100vh}
aside,section{background:#191f2b;border:1px solid #2a3242;border-radius:16px;padding:20px}
h1{font-size:30px;margin:0 0 4px}h2{font-size:16px;margin:0 0 14px}.muted{color:#a8b2c4;font-size:13px;line-height:1.5}
#status{padding:10px;border-radius:10px;background:#222b3a;margin:16px 0}#skills{padding-left:20px;max-height:300px;overflow:auto}
#chat{display:flex;flex-direction:column;min-height:calc(100vh - 100px)}#messages{flex:1;overflow:auto;display:flex;flex-direction:column;gap:12px;margin:14px 0}
.bubble{padding:12px 14px;border-radius:12px;max-width:88%;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.5}
.user{align-self:flex-end;background:#33476a}.assistant{align-self:flex-start;background:#222b3a}.system{align-self:stretch;background:#332d22;color:#f3dca8;font-size:13px}
form{display:flex;gap:8px}input{flex:1;min-width:0;background:#0e1118;color:#fff;border:1px solid #3b4558;border-radius:10px;padding:13px;font:inherit}
button{background:#b8d1ff;color:#10131a;border:0;border-radius:10px;padding:11px 16px;font:600 14px system-ui;cursor:pointer}button:disabled{opacity:.5;cursor:wait}
@media(max-width:720px){main{grid-template-columns:1fr;padding:12px}#chat{min-height:65vh}}
</style></head><body><main>
<aside><h1>Yuki</h1><div class="muted">Personal AI · local-first supervision</div>
<div id="status" class="muted">Checking runtime…</div>
<h2>Registered skills</h2><ul id="skills"></ul>
<p class="muted">Skills are visible here but are not executed from desktop chat. Consequential actions remain behind Yuki's guarded approval flow.</p>
<p class="muted" id="privacy">Cloud requests require confirmation before sending. Never submit passwords, API keys, or sensitive information.</p>
<button onclick="refreshStatus()">Refresh status</button></aside>
<section id="chat"><h2>Conversation</h2><div id="messages" aria-live="polite"><div class="bubble system">Welcome to Yuki. Ask a question to begin.</div></div>
<form id="composer"><input id="prompt" maxlength="8000" autocomplete="off" aria-label="Message to Yuki" placeholder="Message Yuki…" /><button id="send" type="submit">Send</button></form>
<div class="muted" id="feedback" role="status"></div></section></main>
<script>
let statusData={provider:"unknown",cloud_confirmation_required:false};
const messages=document.getElementById("messages"), promptBox=document.getElementById("prompt"), send=document.getElementById("send");
function bubble(text,kind){const node=document.createElement("div");node.className="bubble "+kind;node.textContent=text;messages.appendChild(node);messages.scrollTop=messages.scrollHeight}
async function refreshStatus(){try{statusData=await window.pywebview.api.get_status();document.getElementById("status").textContent="Provider: "+statusData.provider+"\n"+statusData.provider_status+"\n"+statusData.message;const list=document.getElementById("skills");list.replaceChildren();for(const name of statusData.skills||[]){const li=document.createElement("li");li.textContent=name;list.appendChild(li)}}catch(e){document.getElementById("status").textContent="Runtime status unavailable."}}
document.getElementById("composer").addEventListener("submit",async event=>{event.preventDefault();const text=promptBox.value.trim();if(!text||send.disabled)return;let approved=false;if(statusData.cloud_confirmation_required){approved=window.confirm("Send this message to the configured cloud provider ("+statusData.provider+")? The prompt will leave this computer and may incur a charge. Continue?");if(!approved){document.getElementById("feedback").textContent="Cancelled. No data sent.";return}}
bubble(text,"user");promptBox.value="";send.disabled=true;document.getElementById("feedback").textContent="Waiting for provider…";try{const result=await window.pywebview.api.ask(text,approved);bubble(result.message,result.ok?"assistant":"system");document.getElementById("feedback").textContent=result.ok?"Response received.":"Request not completed."}catch(e){bubble("Request failed safely. Check provider configuration and retry.","system")}finally{send.disabled=false;promptBox.focus()}});
window.addEventListener("pywebviewready",refreshStatus);
</script></body></html>"""
    webview.create_window(
        "Yuki", html=html, js_api=bridge, width=1180, height=780, min_size=(760, 560)
    )
    webview.start()
