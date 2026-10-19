"""Native OpenAI Chat Completions tool-call adapter (also suitable for compatible APIs)."""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit

class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward a bearer token or tool payload to a redirected origin."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenAIToolCallingProvider:
    def __init__(self, model: str, *, api_key_env: str="OPENAI_API_KEY",
                 base_url: str="https://api.openai.com/v1", timeout: int=45, allow_local_http: bool=False):
        if not isinstance(model,str) or not model.strip(): raise ValueError("model is required")
        if not api_key_env.isidentifier(): raise ValueError("Invalid API-key environment name")
        parts=urlsplit(base_url.rstrip("/"))
        import ipaddress
        host=parts.hostname or ""
        try: loopback=host=="localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError: loopback=False
        secure = parts.scheme=="https" or (allow_local_http and parts.scheme=="http" and loopback)
        if not secure or not host or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("Provider URL must be HTTPS, except explicitly allowed loopback HTTP, without credentials")
        if type(timeout) is not int or not 1<=timeout<=120: raise ValueError("timeout must be 1..120")
        self.model,self.api_key_env,self.base_url,self.timeout=model.strip(),api_key_env,base_url.rstrip("/"),timeout

    def complete_tool_turn(self,messages,tools):
        key=os.environ.get(self.api_key_env,"").strip()
        if not key: raise RuntimeError("Provider API key is not configured")
        payload={"model":self.model,"messages":messages,"tools":tools,"tool_choice":"auto","stream":False}
        request=urllib.request.Request(self.base_url+"/chat/completions",data=json.dumps(payload,allow_nan=False).encode(),
            headers={"Content-Type":"application/json","Accept":"application/json","Authorization":"Bearer "+key},method="POST")
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            with opener.open(request,timeout=self.timeout) as response:
                raw=response.read(2_000_001)
        except (urllib.error.URLError,TimeoutError,OSError) as exc:
            # Redirects are deliberately rejected to prevent credential forwarding.
            raise RuntimeError("Provider request failed or attempted a redirect") from exc
        if len(raw)>2_000_000: raise RuntimeError("Provider response too large")
        try:
            choice=json.loads(raw.decode())["choices"][0]["message"]
        except (UnicodeDecodeError,json.JSONDecodeError,KeyError,IndexError,TypeError) as exc: raise RuntimeError("Provider response malformed") from exc
        calls=choice.get("tool_calls")
        if calls:
            normalized=[]
            for call in calls:
                fn=call.get("function",{})
                try: args=json.loads(fn.get("arguments","{}"))
                except (json.JSONDecodeError,TypeError) as exc: raise RuntimeError("Provider tool arguments malformed") from exc
                if not isinstance(args,dict): raise RuntimeError("Tool arguments must be an object")
                normalized.append({"id":call.get("id"),"name":fn.get("name"),"arguments":args})
            return {"type":"tool_calls","calls":normalized}
        content=choice.get("content")
        if not isinstance(content,str): raise RuntimeError("Provider message missing text")
        return {"type":"message","content":content}
