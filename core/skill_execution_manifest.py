"""Data-only, versioned contract for isolated skill execution. Does not launch code."""
from dataclasses import dataclass
import hashlib, json, re
from typing import Any, Mapping
from core.sandbox_policy import SandboxPolicy, SandboxPolicyError

class SkillManifestError(ValueError): pass
_HEX=re.compile(r"^[0-9a-f]{64}$")
_IMAGE=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:=-]{0,254}@sha256:[0-9a-f]{64}$")
_NAME=re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_CAPS=frozenset({"read_workspace","write_workspace","network_allowlist"})

def _json(v:Any,n:int)->bytes:
 try: b=json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()
 except (TypeError,ValueError,UnicodeError) as e: raise SkillManifestError("invalid JSON data") from e
 if len(b)>n: raise SkillManifestError("JSON field too large")
 return b

def _schema(v:Any):
 if not isinstance(v,dict) or v.get("type")!="object": raise SkillManifestError("schema root must be object")
 _json(v,16384); stack=[(v,0)]
 while stack:
  x,d=stack.pop()
  if d>16: raise SkillManifestError("schema too deep")
  if isinstance(x,dict):
   if "$ref" in x or "$dynamicRef" in x: raise SkillManifestError("schema references forbidden")
   stack.extend((z,d+1) for z in x.values())
  elif isinstance(x,list):
   if len(x)>256: raise SkillManifestError("schema too large")
   stack.extend((z,d+1) for z in x)
  elif not isinstance(x,(str,int,float,bool,type(None))): raise SkillManifestError("invalid schema value")

@dataclass(frozen=True)
class SkillExecutionManifest:
 manifest_version:int
 skill_name:str
 artifact_sha256:str
 entrypoint:str
 input_schema:Mapping[str,Any]
 output_schema:Mapping[str,Any]
 capabilities:frozenset[str]
 image:str
 command:tuple[str,...]
 sandbox_policy:SandboxPolicy
 def validate(self):
  if isinstance(self.manifest_version,bool) or self.manifest_version!=1: raise SkillManifestError("unsupported version")
  if not isinstance(self.skill_name,str) or not _NAME.fullmatch(self.skill_name): raise SkillManifestError("invalid skill name")
  if not isinstance(self.artifact_sha256,str) or not _HEX.fullmatch(self.artifact_sha256): raise SkillManifestError("artifact SHA-256 required")
  p=self.entrypoint
  if not isinstance(p,str) or not p or len(p)>512 or "\x00" in p or "\\" in p or p.startswith("/") or re.match(r"^[A-Za-z]:",p) or any(x in ("",".","..") for x in p.split("/")): raise SkillManifestError("unsafe relative entrypoint")
  if not isinstance(self.image,str) or not _IMAGE.fullmatch(self.image): raise SkillManifestError("immutable image digest required")
  if not isinstance(self.capabilities,frozenset) or any(not isinstance(x,str) for x in self.capabilities) or self.capabilities-_CAPS: raise SkillManifestError("invalid capabilities")
  if not isinstance(self.command,tuple) or not self.command or len(self.command)>32 or any(not isinstance(x,str) or not x or "\x00" in x or len(x)>1024 for x in self.command) or sum(map(len,self.command))>4096: raise SkillManifestError("invalid argv")
  if not isinstance(self.sandbox_policy,SandboxPolicy): raise SkillManifestError("sandbox policy required")
  try: self.sandbox_policy.validate()
  except (SandboxPolicyError,AttributeError) as e: raise SkillManifestError("sandbox policy rejected") from e
  if self.capabilities!=self.sandbox_policy.capabilities: raise SkillManifestError("capability mismatch")
  _schema(self.input_schema); _schema(self.output_schema)
  return self
 def canonical_bytes(self):
  self.validate(); p=self.sandbox_policy
  return _json({"manifest_version":self.manifest_version,"skill_name":self.skill_name,"artifact_sha256":self.artifact_sha256,"entrypoint":self.entrypoint,"input_schema":self.input_schema,"output_schema":self.output_schema,"capabilities":sorted(self.capabilities),"image":self.image,"command":list(self.command),"sandbox_policy":{"network_mode":p.network_mode,"capabilities":sorted(p.capabilities),"timeout_seconds":p.timeout_seconds,"memory_mb":p.memory_mb,"cpu_millis":p.cpu_millis,"max_output_bytes":p.max_output_bytes,"max_processes":p.max_processes,"read_only_root":p.read_only_root,"run_as_non_root":p.run_as_non_root}},65536)
 @property
 def manifest_sha256(self): return hashlib.sha256(self.canonical_bytes()).hexdigest()
