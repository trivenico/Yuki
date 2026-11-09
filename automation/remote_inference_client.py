"""Authenticated HTTPS client for executing inference jobs on a paired remote device.

The client talks only to Yuki's fixed inference endpoints. It rejects redirects,
uses a device bearer credential, bounds response bodies, and never logs secrets.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

@dataclass(frozen=True)
class RemoteInferenceJob:
    job_id: str
    model_id: str
    input_text: str
    lease_expires_at: str | None
    claim_generation: int
    claim_token: str

class RemoteInferenceJobClient:
    def __init__(self, base_url: str, device_token: str, *, lease_seconds: int = 120,
                 timeout: float = 15, max_response_bytes: int = 128_000):
        if not isinstance(base_url,str) or not base_url.strip():
            raise ValueError("invalid_api_url")
        parsed=urlsplit(base_url.strip().rstrip("/"))
        host=parsed.hostname
        loopback=host in {"localhost","127.0.0.1","::1"}
        if (parsed.scheme!="https" and not (parsed.scheme=="http" and loopback)
            or not host or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("","/")):
            raise ValueError("Yuki API must use HTTPS or loopback HTTP")
        if not isinstance(device_token,str) or not 20<=len(device_token)<=256:
            raise ValueError("invalid_device_credential")
        if type(lease_seconds) is not int or not 15<=lease_seconds<=3600:
            raise ValueError("invalid_lease_seconds")
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not 1<=timeout<=60:
            raise ValueError("invalid_timeout")
        if type(max_response_bytes) is not int or not 1024<=max_response_bytes<=512_000:
            raise ValueError("invalid_response_limit")
        self.base_url=base_url.strip().rstrip("/")
        self.device_token=device_token
        self.lease_seconds=lease_seconds
        self.timeout=timeout
        self.max_response_bytes=max_response_bytes

    def _request(self,path,payload=None):
        data=None if payload is None else json.dumps(payload,separators=(",",":")).encode("utf-8")
        request=urllib.request.Request(self.base_url+path,data=data,method="POST",headers={
            "Authorization":"Bearer "+self.device_token,
            "Accept":"application/json",
            "Content-Type":"application/json",
        })
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,req,fp,code,msg,headers,newurl): return None
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=self.timeout) as response:
                raw=response.read(self.max_response_bytes+1)
                if len(raw)>self.max_response_bytes: raise RuntimeError("response_too_large")
                body=json.loads(raw.decode("utf-8"))
                if not isinstance(body,dict): raise RuntimeError("invalid_api_response")
                return body
        except urllib.error.HTTPError as exc:
            # Never include response bodies or request details in raised errors.
            if exc.code in (401,403): raise PermissionError("device_authentication_failed") from None
            if exc.code==404: raise LookupError("inference_job_not_found") from None
            if exc.code in (400,409): raise ValueError("inference_job_rejected") from None
            raise RuntimeError("Yuki inference API request failed") from None
        except (urllib.error.URLError,TimeoutError,OSError,json.JSONDecodeError,UnicodeDecodeError):
            raise RuntimeError("Yuki inference API unavailable or returned invalid data") from None

    def claim_next(self,device_token=None):
        if device_token is not None and device_token!=self.device_token:
            raise PermissionError("device_credential_mismatch")
        body=self._request("/v1/inference/claim")
        raw=body.get("job")
        if raw is None: return None
        if not isinstance(raw,dict) or set(raw)!={"job_id","model_id","input_text","lease_expires_at","claim_generation","claim_token"}:
            raise RuntimeError("invalid_claim_response")
        if not all(isinstance(raw[k],str) for k in ("job_id","model_id","input_text")):
            raise RuntimeError("invalid_claim_response")
        if raw["lease_expires_at"] is not None and not isinstance(raw["lease_expires_at"],str):
            raise RuntimeError("invalid_claim_response")
        if type(raw["claim_generation"]) is not int or raw["claim_generation"] < 1:
            raise RuntimeError("invalid_claim_response")
        if not isinstance(raw["claim_token"],str) or not 32 <= len(raw["claim_token"]) <= 128:
            raise RuntimeError("invalid_claim_response")
        return RemoteInferenceJob(**raw)

    def complete_job(self,device_token,job_id,*,claim_generation,claim_token,result_text=None,error_code=None):
        if device_token!=self.device_token: raise PermissionError("device_credential_mismatch")
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("invalid_job_id")
        if type(claim_generation) is not int or claim_generation < 1: raise ValueError("invalid_claim_generation")
        if not isinstance(claim_token,str) or not 32 <= len(claim_token) <= 128: raise ValueError("invalid_claim_token")
        if (result_text is None)==(error_code is None): raise ValueError("provide_exactly_one_result_or_error")
        if result_text is not None and (not isinstance(result_text,str) or len(result_text.encode("utf-8"))>20_000):
            raise ValueError("result_too_large")
        if error_code is not None and (not isinstance(error_code,str) or len(error_code)>64):
            raise ValueError("invalid_error_code")
        body={"claim_generation":claim_generation,"claim_token":claim_token}
        body["result_text" if result_text is not None else "error_code"] = result_text if result_text is not None else error_code
        response=self._request(f"/v1/inference/jobs/{job_id}/complete",body)
        if not isinstance(response.get("job"),dict): raise RuntimeError("invalid_completion_response")
        return response["job"]
