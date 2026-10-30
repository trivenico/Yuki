# Ollama setup and real-inference smoke test

Yuki defaults to local Ollama. Unit tests and packaged-launch checks do **not** prove that Ollama is installed, the configured model is present, or real inference works on your machine. This opt-in smoke test sends one short prompt to the configured Ollama provider. It does not install software, pull models, or change your configuration.

## Windows

1. Install and start [Ollama for Windows](https://ollama.com/download).
2. Open PowerShell and pull the default model if you have not already:

```powershell
ollama pull llama3.2:3b
ollama list
```

3. From the Yuki repository root, set the local provider/model and run the smoke test:

```powershell
$env:YUKI_PROVIDER = "ollama"
$env:YUKI_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
$env:YUKI_OLLAMA_MODEL = "llama3.2:3b"
python scripts/smoke_test_ollama.py
```

## Linux

1. Install Ollama using the official Linux instructions: [Ollama Linux download](https://ollama.com/download/linux).
2. Start Ollama and pull the default model:

```bash
ollama pull llama3.2:3b
ollama list
```

3. From the Yuki repository root:

```bash
export YUKI_PROVIDER=ollama
export YUKI_OLLAMA_BASE_URL=http://127.0.0.1:11434
export YUKI_OLLAMA_MODEL=llama3.2:3b
python scripts/smoke_test_ollama.py
```

## Expected result

A successful run prints:

```text
PASS: real Ollama inference returned the expected smoke-test sentinel.
```

The script returns a non-zero exit code if the configured provider is not Ollama, the model is missing, Ollama is unreachable, inference fails, or the response omits the expected sentinel. It never falls back to a mock or cloud provider.

## Troubleshooting

- **Ollama is unreachable:** confirm the Ollama service is running and listening at `127.0.0.1:11434`.
- **Model is missing:** run `ollama list`; pull `llama3.2:3b` or set `YUKI_OLLAMA_MODEL` to a model you already installed.
- **Slow first inference:** the model may need to load into memory. Check available RAM/VRAM and Ollama logs before choosing a larger model.
- **Configuration mismatch:** this script reads Yuki's validated configuration, including `YUKI_PROVIDER`, `YUKI_OLLAMA_BASE_URL`, and `YUKI_OLLAMA_MODEL` overrides.
- **Sharing logs:** do not post API keys, credentials, private prompts, personal files, or sensitive model output in an issue.

## What this proves—and what it does not

A pass confirms endpoint availability, model availability, and one real inference request returning the expected marker. It does not certify desktop/CLI integration, long-running stability, performance, screen/camera permissions, or production readiness. The smoke test is intentionally excluded from ordinary CI because it requires a separately running Ollama service and a downloaded model.
