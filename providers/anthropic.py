"""Anthropic Messages API adapter; credentials remain in environment variables."""
from __future__ import annotations
import json, os, urllib.error, urllib.request
from urllib.parse import urlsplit
class AnthropicProvider:
    def __init__(self, model: str, *, api_key_env: str = "ANTHROPIC_API_KEY", base_url: str = "https://api.anthropic.com", timeout: float = 45, approval_callback=None):
        if not isinstance(model,str) or not model.strip() or len(model)>120: raise ValueError("Configure a valid Anthropic model name.")
        if not isinstance(api_key_env,str) or not api_key_env.isidentifier(): raise ValueError("API key environment variable name is invalid.")
        parsed=urlsplit(base_url.rstrip("/")) if isinstance(base_url,str) else None
        if parsed is None or parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment: raise ValueError("Anthropic endpoint must be HTTPS without embedded credentials.")
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not 1<=timeout<=120: raise ValueError("Timeout must be between 1 and 120 seconds.")
        self.model,self.api_key_env,self.base_url,self.timeout=model.strip(),api_key_env,base_url.rstrip("/"),timeout
        self.approval_callback = approval_callback
    def is_available(self)->bool: return bool(os.environ.get(self.api_key_env,"").strip())
    def generate(self,prompt:str)->str:
        if not isinstance(prompt,str) or not prompt.strip(): raise ValueError("Prompt must be non-empty text.")
        key=os.environ.get(self.api_key_env,"").strip()
        if self.approval_callback is None or self.approval_callback(prompt.strip()) is not True:
            raise RuntimeError("External provider transmission was not explicitly approved.")
        if not key: raise RuntimeError(f"Cloud provider is not configured; set {self.api_key_env}.")
        payload=json.dumps({"model":self.model,"max_tokens":2048,"messages":[{"role":"user","content":prompt.strip()[:20000]}]}).encode()
        req=urllib.request.Request(self.base_url+"/v1/messages",data=payload,method="POST",headers={"content-type":"application/json","accept":"application/json","x-api-key":key,"anthropic-version":"2023-06-01"})
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as response: raw=response.read(2_000_001)
        except (urllib.error.URLError,TimeoutError,OSError) as exc: raise RuntimeError("Anthropic request failed.") from exc
        if len(raw)>2_000_000: raise RuntimeError("Anthropic response exceeded the 2 MB limit.")
        try:
            body=json.loads(raw.decode("utf-8")); chunks=body["content"]; text="\n".join(item["text"] for item in chunks if isinstance(item,dict) and item.get("type")=="text")
        except (UnicodeDecodeError,json.JSONDecodeError,KeyError,TypeError) as exc: raise RuntimeError("Anthropic returned an unsupported response.") from exc
        self.last_usage={"prompt_tokens":body.get("usage",{}).get("input_tokens"),"completion_tokens":body.get("usage",{}).get("output_tokens")}
        if not text.strip(): raise RuntimeError("Anthropic returned no text response.")
        return text.strip()
