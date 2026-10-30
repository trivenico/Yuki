"""Local OpenAI-compatible inference endpoint adapter.

Intended for loopback servers such as llama.cpp, vLLM, SGLang, TGI and MLX-LM.
Only loopback HTTP or HTTPS is accepted; no remote host or redirect is followed.
"""
from __future__ import annotations
import ipaddress
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class LocalOpenAICompatibleProvider:
    def __init__(self, model, *, base_url="http://127.0.0.1:8000/v1",
                 timeout=60, api_key=None, max_prompt_chars=20000):
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError("A valid served model name is required.")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("A local inference endpoint is required.")
        try:
            parsed=urlsplit(base_url.strip().rstrip("/"))
            host=parsed.hostname or ""
            parsed.port
            loopback=host.lower()=="localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError as exc:
            raise ValueError("Invalid local inference endpoint.") from exc
        if (parsed.scheme not in {"http","https"} or not loopback or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Local inference endpoint must be loopback HTTP(S), without URL credentials.")
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not 1<=timeout<=180:
            raise ValueError("timeout must be between 1 and 180 seconds")
        if api_key is not None and (not isinstance(api_key,str) or len(api_key)>4096
                                    or "\r" in api_key or "\n" in api_key):
            raise ValueError("Invalid local endpoint API key")
        if isinstance(max_prompt_chars,bool) or not isinstance(max_prompt_chars,int) or not 1<=max_prompt_chars<=100000:
            raise ValueError("max_prompt_chars must be between 1 and 100000")
        self.model=model.strip()
        self.base_url=base_url.strip().rstrip("/")
        self.timeout=timeout
        self.api_key=api_key
        self.max_prompt_chars=max_prompt_chars

    def is_available(self):
        request=urllib.request.Request(self.base_url+"/models",headers={"Accept":"application/json"})
        if self.api_key:
            request.add_header("Authorization","Bearer "+self.api_key)
        try:
            opener=urllib.request.build_opener(_NoRedirect())
            with opener.open(request,timeout=min(self.timeout,4)) as response:
                raw=response.read(500001)
            if len(raw)>500000:
                return False
            payload=json.loads(raw.decode("utf-8"))
            data=payload.get("data") if isinstance(payload,dict) else None
            return isinstance(data,list) and any(
                isinstance(item,dict) and item.get("id")==self.model for item in data
            )
        except (urllib.error.URLError,TimeoutError,OSError,UnicodeDecodeError,json.JSONDecodeError):
            return False

    def generate(self,prompt):
        if not isinstance(prompt,str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        if len(prompt)>self.max_prompt_chars:
            raise ValueError("Prompt exceeds configured character limit.")
        payload=json.dumps({
            "model":self.model,
            "messages":[{"role":"user","content":prompt.strip()}],
            "stream":False,
        }).encode("utf-8")
        request=urllib.request.Request(
            self.base_url+"/chat/completions",data=payload,
            headers={"Content-Type":"application/json","Accept":"application/json"},
            method="POST",
        )
        if self.api_key:
            request.add_header("Authorization","Bearer "+self.api_key)
        try:
            opener=urllib.request.build_opener(_NoRedirect())
            with opener.open(request,timeout=self.timeout) as response:
                raw=response.read(2000001)
        except (urllib.error.URLError,TimeoutError,OSError) as exc:
            raise RuntimeError("Local inference request failed; redirects are disabled.") from exc
        if len(raw)>2000000:
            raise RuntimeError("Local inference response exceeded the 2 MB limit.")
        try:
            body=json.loads(raw.decode("utf-8"))
            text=body["choices"][0]["message"]["content"]
        except (UnicodeDecodeError,json.JSONDecodeError,KeyError,IndexError,TypeError) as exc:
            raise RuntimeError("Local inference server returned an unsupported response.") from exc
        if not isinstance(text,str) or not text.strip():
            raise RuntimeError("Local inference server returned no text response.")
        return text.strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None
