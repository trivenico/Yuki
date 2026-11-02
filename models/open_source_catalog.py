"""Curated open-weight model families and open-source inference runtimes.

This is a discovery seed, not a license allow-list or quality ranking. Exact
model revisions must be inspected before download and activation.
"""
MODEL_FAMILIES = (
    {"family":"Qwen","organization":"Alibaba/Qwen","tasks":["general","coding","multilingual"],"model_ids":["Qwen/Qwen3-8B","Qwen/Qwen3-4B","Qwen/Qwen3-Coder-Next"],"hub":"https://huggingface.co/Qwen","license_policy":"verify_each_checkpoint"},
    {"family":"DeepSeek","organization":"DeepSeek","tasks":["reasoning","coding","distillation"],"model_ids":["deepseek-ai/DeepSeek-R1-Distill-Qwen-7B","deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct"],"hub":"https://huggingface.co/deepseek-ai","license_policy":"verify_each_checkpoint"},
    {"family":"Mistral","organization":"Mistral AI","tasks":["general","coding","multilingual"],"model_ids":["mistralai/Mistral-7B-Instruct-v0.3","mistralai/Ministral-8B-Instruct-2410"],"hub":"https://huggingface.co/mistralai","license_policy":"verify_each_checkpoint"},
    {"family":"Gemma","organization":"Google","tasks":["general","reasoning","multimodal"],"model_ids":["google/gemma-3-4b-it","google/gemma-3-1b-it"],"hub":"https://huggingface.co/google","license_policy":"verify_each_checkpoint"},
    {"family":"Llama","organization":"Meta","tasks":["general","reasoning","multilingual"],"model_ids":["meta-llama/Llama-3.2-3B-Instruct","meta-llama/Llama-3.1-8B-Instruct"],"hub":"https://huggingface.co/meta-llama","license_policy":"verify_each_checkpoint_and_access_terms"},
    {"family":"Phi","organization":"Microsoft","tasks":["general","coding","on_device"],"model_ids":["microsoft/Phi-4-mini-instruct","microsoft/Phi-3.5-mini-instruct"],"hub":"https://huggingface.co/microsoft","license_policy":"verify_each_checkpoint"},
    {"family":"OLMo","organization":"Allen Institute for AI","tasks":["research","general","reproducibility"],"model_ids":["allenai/OLMo-2-1124-7B-Instruct","allenai/OLMo-2-1124-7B"],"hub":"https://huggingface.co/allenai","license_policy":"verify_each_checkpoint"},
    {"family":"gpt-oss","organization":"OpenAI","tasks":["reasoning","tool_use"],"model_ids":["openai/gpt-oss-20b","openai/gpt-oss-120b"],"hub":"https://huggingface.co/openai","license_policy":"verify_each_checkpoint_and_usage_policy"},
)

INFERENCE_RUNTIMES = (
    {"name":"Ollama","repo":"https://github.com/ollama/ollama","license":"MIT","integration":"native_ollama_provider","endpoint_default":"http://127.0.0.1:11434"},
    {"name":"llama.cpp","repo":"https://github.com/ggml-org/llama.cpp","license":"MIT","integration":"local_openai_compatible","endpoint_default":"http://127.0.0.1:8080/v1"},
    {"name":"vLLM","repo":"https://github.com/vllm-project/vllm","license":"Apache-2.0","integration":"local_openai_compatible","endpoint_default":"http://127.0.0.1:8000/v1"},
    {"name":"SGLang","repo":"https://github.com/sgl-project/sglang","license":"Apache-2.0","integration":"local_openai_compatible","endpoint_default":"http://127.0.0.1:30000/v1"},
    {"name":"Text Generation Inference","repo":"https://github.com/huggingface/text-generation-inference","license":"Apache-2.0","integration":"verify_openai_compatibility_per_release","endpoint_default":None},
    {"name":"MLX-LM","repo":"https://github.com/ml-explore/mlx-lm","license":"MIT","integration":"local_openai_compatible_where_server_mode_supported","endpoint_default":"http://127.0.0.1:8080/v1"},
)
