# Local model setup

Yuki now defaults to a local Ollama provider. It does not download models automatically and it does not silently send prompts to a cloud provider.

## First-time setup

1. Install Ollama for your operating system.
2. Start the Ollama service.
3. Download the configured model yourself:

   ```sh
   ollama pull qwen2.5:7b
   ```

4. Run Yuki's diagnostics and start the application.

The default endpoint is `http://127.0.0.1:11434`; local loopback HTTP is accepted. Remote Ollama endpoints must use HTTPS.

## Explicit cloud use

Set `provider: openai` and provide `OPENAI_API_KEY` in the process environment to select OpenAI explicitly. Set `provider: gemini` to use the Gemini CLI adapter. Set `provider: hybrid` only when you deliberately configure `YUKI_CLOUD_MODEL` and the comma-separated `YUKI_CLOUD_TASKS` allow-list. Hybrid mode keeps non-allow-listed tasks local and does not automatically fall back to cloud when local inference fails.

## Model and hardware notes

The default `qwen2.5:7b` model is a practical local starting point, not a claim that it is Yuki's first-party foundation model. Hardware requirements depend on quantization, context length, concurrency and operating system. Choose a smaller Ollama model on constrained devices and configure `ollama_model` or `YUKI_OLLAMA_MODEL` accordingly.

Yuki's first-party Yuki-Core training and verified local inference work remain separate research tracks. The small byte-level Yuki-Core prototype is not yet a capable general-purpose assistant.
