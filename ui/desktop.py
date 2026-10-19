"""Optional PyWebView desktop shell over the same in-process Yuki state.

The bridge exposes read-only status only. Sensitive actions remain in the approval-gated
terminal until a separately authenticated action API is designed and audited.
"""
from __future__ import annotations
import json

def run_desktop(agent) -> None:
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError("Install the optional desktop dependency with: pip install pywebview") from exc

    class DesktopBridge:
        def get_status(self):
            try:
                registry = getattr(agent, "registry", None)
                names = registry.names() if registry is not None else []
                skills = agent.available_skills() if callable(getattr(agent, "available_skills", None)) else []
                return {"ready": True, "skills": names, "skill_count": len(skills),
                        "message": "Read-only desktop view. Approvals and consequential actions remain in the terminal."}
            except Exception:
                return {"ready": False, "skills": [], "skill_count": 0, "message": "Runtime status unavailable."}

    html = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font:16px system-ui;background:#10131a;color:#edf2fa;margin:0;padding:32px}main{max-width:850px;margin:auto}section{background:#1a202c;border-radius:14px;padding:20px;margin:16px 0}h1{font-size:30px}button{padding:10px 14px;border-radius:8px;border:0;cursor:pointer}li{padding:4px}</style></head>
<body><main><h1>Yuki</h1><p>Personal AI · local-first supervision</p><section><h2>Runtime status</h2><p id="status">Loading runtime…</p><p id="message"></p><button onclick="refresh()">Refresh</button></section><section><h2>Registered skills</h2><ul id="skills"></ul></section><script>
async function refresh(){try{const s=await window.pywebview.api.get_status();document.getElementById('status').textContent=s.ready?'Ready':'Unavailable';document.getElementById('message').textContent=s.message;document.getElementById('skills').innerHTML='';for(const n of s.skills){const li=document.createElement('li');li.textContent=n;document.getElementById('skills').appendChild(li)}}catch(e){document.getElementById('status').textContent='Bridge unavailable'}}
window.addEventListener('pywebviewready',refresh);
</script></main></body></html>"""
    webview.create_window("Yuki", html=html, js_api=DesktopBridge(), width=1100, height=760, min_size=(760, 520))
    webview.start()
