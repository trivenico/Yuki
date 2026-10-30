"""Review-first, pinned-revision Hugging Face model snapshot downloader.

Downloads safetensors and tokenizer/config assets only. No remote code is
executed and no provider/model is activated by this module.
"""
from __future__ import annotations
import re
from pathlib import Path

_MODEL_ID=re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_REVISION=re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_FILES=[
 "config.json","generation_config.json","tokenizer.json","tokenizer_config.json",
 "special_tokens_map.json","added_tokens.json","chat_template.jinja",
 "model.safetensors","model-*.safetensors","*.safetensors.index.json",
 "*.model","*.tiktoken","vocab.json","merges.txt","vocab.txt",
 "spiece.model","sentencepiece.bpe.model","preprocessor_config.json",
]
class ModelDownloadError(RuntimeError):
    pass

def download_model_snapshot(model_id,revision,cache_dir,*,max_bytes=20*1024**3,token=None):
    if not isinstance(model_id,str) or not _MODEL_ID.fullmatch(model_id):
        raise ValueError("model_id must be an exact owner/name identifier")
    if not isinstance(revision,str) or not _REVISION.fullmatch(revision):
        raise ValueError("A pinned 40-character commit revision is required; mutable branches are not accepted")
    if isinstance(max_bytes,bool) or not isinstance(max_bytes,int) or not 1<=max_bytes<=200*1024**3:
        raise ValueError("max_bytes must be between 1 byte and 200 GiB")
    if token is not None and (not isinstance(token,str) or not token.strip() or len(token)>4096 or any(c in token for c in "\r\n")):
        raise ValueError("Invalid Hub token")
    try:
        from huggingface_hub import HfApi, snapshot_download
    except ImportError as exc:
        raise ModelDownloadError("Install huggingface_hub from requirements-training.txt first") from exc
    try:
        info=HfApi(token=token).model_info(model_id,revision=revision,files_metadata=True)
        siblings=getattr(info,"siblings",None) or []
        sizes=[]
        for item in siblings:
            name=getattr(item,"rfilename","")
            size=getattr(item,"size",None)
            if any(Path(name).match(pattern) for pattern in _ALLOWED_FILES) and isinstance(size,int) and size>=0:
                sizes.append((name,size))
        if not any(name.endswith(".safetensors") for name,_ in sizes):
            raise ModelDownloadError("Pinned model revision does not expose safetensors weights")
        estimated=sum(size for _,size in sizes)
        if estimated>max_bytes:
            raise ModelDownloadError("Model snapshot exceeds configured download budget")
        path=snapshot_download(
            repo_id=model_id,revision=revision,cache_dir=str(Path(cache_dir).expanduser()),
            allow_patterns=_ALLOWED_FILES,token=token,
            max_workers=4,
        )
        root=Path(path).resolve()
        config=root/"config.json"
        weights=list(root.glob("*.safetensors"))
        if not config.is_file() or not weights:
            raise ModelDownloadError("Downloaded snapshot is missing config.json or safetensors weights")
        total=sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        if total>max_bytes:
            raise ModelDownloadError("Downloaded snapshot exceeded configured byte budget")
        return {"model_id":model_id,"revision":revision,"local_path":str(root),
                "bytes":total,"weight_files":len(weights),"trust_remote_code":False,
                "automatic_activation":False,"review_status":"downloaded_requires_human_review"}
    except ModelDownloadError:
        raise
    except Exception as exc:
        raise ModelDownloadError("Hugging Face model snapshot download failed") from exc
