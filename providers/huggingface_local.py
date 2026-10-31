"""Optional local Transformers inference adapter.

Loads only an already-downloaded local checkpoint. Remote model code is never
trusted or executed. Install the optional training/runtime dependencies first.
"""
from __future__ import annotations
import importlib.util
from pathlib import Path

class HuggingFaceRuntimeError(RuntimeError):
    pass

class HuggingFaceLocalProvider:
    def __init__(self, model_path, *, device="auto", max_new_tokens=512,
                 max_prompt_chars=20000, torch_dtype="auto"):
        path=Path(model_path).expanduser()
        if not path.exists() or not path.is_dir():
            raise ValueError("model_path must point to an existing local model directory")
        if not (path/"config.json").is_file():
            raise ValueError("Local model directory must contain config.json")
        if not any(path.glob("*.safetensors")):
            raise ValueError("Local model must contain safetensors weights")
        if device not in {"auto","cpu","cuda"}:
            raise ValueError("device must be auto, cpu, or cuda")
        if type(max_new_tokens) is not int or not 1<=max_new_tokens<=4096:
            raise ValueError("max_new_tokens must be between 1 and 4096")
        if type(max_prompt_chars) is not int or not 1<=max_prompt_chars<=100000:
            raise ValueError("max_prompt_chars must be between 1 and 100000")
        if torch_dtype not in {"auto","float16","bfloat16","float32"}:
            raise ValueError("Unsupported torch_dtype")
        self.model_path=path.resolve()
        self.device=device
        self.max_new_tokens=max_new_tokens
        self.max_prompt_chars=max_prompt_chars
        self.torch_dtype=torch_dtype
        self._tokenizer=None
        self._model=None
        self._torch=None

    def is_available(self):
        return (importlib.util.find_spec("torch") is not None
                and importlib.util.find_spec("transformers") is not None)

    def _load(self):
        if self._model is not None:
            return
        if not self.is_available():
            raise HuggingFaceRuntimeError(
                "Local inference requires the optional PyTorch and Transformers packages."
            )
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            dtype_map={"auto":"auto","float16":torch.float16,
                       "bfloat16":torch.bfloat16,"float32":torch.float32}
            self._tokenizer=AutoTokenizer.from_pretrained(
                str(self.model_path),local_files_only=True,trust_remote_code=False
            )
            kwargs={"local_files_only":True,"trust_remote_code":False,
                    "use_safetensors":True,"torch_dtype":dtype_map[self.torch_dtype]}
            if self.device=="auto":
                if torch.cuda.is_available():
                    kwargs["device_map"]="auto"
            elif self.device=="cuda":
                if not torch.cuda.is_available():
                    raise HuggingFaceRuntimeError("CUDA was requested but is unavailable.")
                kwargs["device_map"]="auto"
            else:
                kwargs["device_map"]={"":"cpu"}
            self._model=AutoModelForCausalLM.from_pretrained(str(self.model_path),**kwargs)
            self._model.eval()
            self._torch=torch
        except HuggingFaceRuntimeError:
            raise
        except Exception as exc:
            self._model=None
            self._tokenizer=None
            raise HuggingFaceRuntimeError("Could not load the local Transformers checkpoint.") from exc

    def generate(self,prompt: str)->str:
        if not isinstance(prompt,str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        prompt=prompt.strip()
        if len(prompt)>self.max_prompt_chars:
            raise ValueError("Prompt exceeds configured character limit.")
        self._load()
        try:
            if getattr(self._tokenizer,"chat_template",None):
                messages=[{"role":"user","content":prompt}]
                inputs=self._tokenizer.apply_chat_template(
                    messages,add_generation_prompt=True,return_tensors="pt",
                    return_dict=True
                )
            else:
                inputs=self._tokenizer(prompt,return_tensors="pt")
            device=getattr(self._model,"device",None)
            if device is not None:
                inputs={k:v.to(device) for k,v in inputs.items()}
            input_length=inputs["input_ids"].shape[-1]
            with self._torch.inference_mode():
                output=self._model.generate(
                    **inputs,max_new_tokens=self.max_new_tokens,
                    do_sample=False,pad_token_id=(
                        self._tokenizer.pad_token_id
                        if self._tokenizer.pad_token_id is not None
                        else self._tokenizer.eos_token_id
                    ),
                )
            generated=output[0][input_length:]
            answer=self._tokenizer.decode(generated,skip_special_tokens=True)
            if not isinstance(answer,str) or not answer.strip():
                raise HuggingFaceRuntimeError("The local model returned an empty response.")
            return answer.strip()
        except HuggingFaceRuntimeError:
            raise
        except Exception as exc:
            raise HuggingFaceRuntimeError("Local Transformers generation failed.") from exc
