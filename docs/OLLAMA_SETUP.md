# Ollama provider setup and smoke test

This is an opt-in real-provider check. Unit tests mock HTTP responses; they do not prove that an Ollama server, model download, or local inference works on your machine.

## Windows PowerShell

1. Install Ollama for Windows and start it.
2. Pull the default model:

```powershell
ollama pull llama3.2:3b
ollama list
```

3. From the Yuki repository root, configure and run the smoke test:

```powershell
$env:YUKI_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
$env:YUKI_OLLAMA_MODEL = "llama3.2:3b"
$env:YUKI_PROVIDER_TIMEOUT_SECONDS = "45"
python scripts/smoke_test_ollama.py
```

Expected success output ends with:

```text
PASS: Ollama is reachable, the model is installed, and real inference succeeded.
```

## Linux and macOS

Start Ollama using your platform's installation method, then run:

```bash
ollama pull llama3.2:3b
export YUKI_OLLAMA_BASE_URL=http://127.0.0.1:11434
export YUKI_OLLAMA_MODEL=llama3.2:3b
export YUKI_PROVIDER_TIMEOUT_SECONDS=45
python scripts/smoke_test_ollama.py
```

## Troubleshooting

- **Unavailable endpoint:** confirm Ollama is running and listening on `127.0.0.1:11434`.
- **Model not installed:** run `ollama pull llama3.2:3b`, or set `YUKI_OLLAMA_MODEL` to a model shown by `ollama list`.
- **Slow first inference:** the model may need to load into memory. Try a larger timeout with `--timeout 90`.
- **Configuration error:** the provider accepts loopback HTTP or HTTPS endpoints only, and the timeout must be between 1 and 120 seconds.
- **Smoke test fails:** retain the exit code and sanitized error text for debugging. Do not paste API keys, credentials, private prompts, or sensitive model output into an issue.

## What this test establishes

A successful run confirms endpoint reachability, presence of the configured model, and one real inference request returning the expected marker. It does not by itself certify desktop/CLI integration, long-running stability, performance, or production readiness.

The smoke test is intentionally excluded from ordinary CI because it requires a separately running Ollama service and downloaded model.