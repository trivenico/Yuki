"""OpenAI-compatible chat adapter for OpenAI, DeepSeek, GLM and local endpoints."""
from __future__ import annotations
import json,os,urllib.error,urllib.request
from urllib.parse import urlsplit
class OpenAICompatibleProvider:
    def __init__(self,model,*,api_key_env="OPENAI_API_KEY",base_url="https://api.openai.com/v1",timeout=45,allow_http_loopback=False,environ=None,approval_callback=None):
        if not isinstance(model,str) or not model.strip() or len(model)>120: raise ValueError("Configure a valid model name.")
        if not isinstance(api_key_env,str) or not api_key_env.isidentifier(): raise ValueError("API key environment variable name is invalid.")
        parsed=urlsplit(base_url.rstrip("/")) if isinstance(base_url,str) else None
        if parsed is None or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment: raise ValueError("Endpoint URL is invalid.")
        loopback=parsed.hostname=="localhost"
        if not loopback:
            import ipaddress
            try: loopback=ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError: pass
        if parsed.scheme!="https" and not (allow_http_loopback and parsed.scheme=="http" and loopback): raise ValueError("Cloud endpoints must use HTTPS; HTTP is allowed only for configured loopback endpoints.")
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not 1<=timeout<=120: raise ValueError("Timeout must be between 1 and 120 seconds.")
        self.model,self.api_key_env,self.base_url,self.timeout=model.strip(),api_key_env,base_url.rstrip("/"),timeout; self._environ=environ
        self.approval_callback = approval_callback
    def _env(self): return os.environ if self._environ is None else self._environ
    def is_available(self):
        if self.api_key_env=="YUKI_LOCAL_DUMMY_KEY" and urlsplit(self.base_url).hostname in {"localhost","127.0.0.1","::1"}: return True
        return bool(self._env().get(self.api_key_env,"").strip())
    def generate(self,prompt:str)->str:
        if not isinstance(prompt,str) or not prompt.strip(): raise ValueError("Prompt must be non-empty text.")
        key=self._env().get(self.api_key_env,"").strip()
        if self.api_key_env != "YUKI_LOCAL_DUMMY_KEY":
            if self.approval_callback is None or self.approval_callback(prompt.strip()) is not True:
                raise RuntimeError("External provider transmission was not explicitly approved.")
        if self.api_key_env!="YUKI_LOCAL_DUMMY_KEY" and not key: raise RuntimeError(f"Provider is not configured; set {self.api_key_env}.")
        payload=json.dumps({"model":self.model,"messages":[{"role":"user","content":prompt.strip()[:20000]}],"stream":False}).encode()
        headers={"Content-Type":"application/json","Accept":"application/json"}
        if key and self.api_key_env!="YUKI_LOCAL_DUMMY_KEY": headers["Authorization"]="Bearer "+key
        req=urllib.request.Request(self.base_url+"/chat/completions",data=payload,headers=headers,method="POST")
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as response: raw=response.read(2_000_001)
        except (urllib.error.URLError,TimeoutError,OSError) as exc: raise RuntimeError("Provider request failed.") from exc
        if len(raw)>2_000_000: raise RuntimeError("Provider response exceeded the 2 MB limit.")
        try: body=json.loads(raw.decode("utf-8")); text=body["choices"][0]["message"]["content"]
        except (UnicodeDecodeError,json.JSONDecodeError,KeyError,IndexError,TypeError) as exc: raise RuntimeError("Provider returned an unsupported response.") from exc
        usage=body.get("usage",{}); self.last_usage={"prompt_tokens":usage.get("prompt_tokens",usage.get("input_tokens")),"completion_tokens":usage.get("completion_tokens",usage.get("output_tokens"))}
        if not isinstance(text,str) or not text.strip(): raise RuntimeError("Provider returned no text response.")
        return text.strip()
