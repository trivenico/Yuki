# Yuki desktop app (early MVP)

## What it is

Run `python desktop_app.py` to open the optional Tkinter desktop client. It uses the existing `DesktopService` and `ProviderManager`; it does not create a parallel provider implementation.

Current MVP capabilities:

- Choose an implemented provider and explicitly run a connectivity check.
- Complete a resumable first-run flow: welcome, provider verification, privacy acknowledgement, permissions acknowledgement, first conversation.
- Send bounded text prompts to the selected real provider.
- See workspace/provider readiness and actionable setup errors.
- Keep provider credentials out of onboarding state and UI fields.

## Configure a provider

The desktop app does not ask users to paste API keys into Yuki's own UI and does not save credentials in the onboarding JSON. Configure credentials through the provider's supported environment or official login flow before launching the app.

For OpenAI on macOS/Linux, set `OPENAI_API_KEY` in the launching shell; for Windows PowerShell, set it in the current session using the platform's environment-variable command. Never put secrets in `config.yaml`, issue text, screenshots, or logs. Gemini CLI uses its supported CLI login. Ollama requires a locally running Ollama service and a configured model.

Select an implemented provider in the sidebar and choose **Verify connection**. Yuki sends a short, explicit connectivity-check prompt; this may incur a small provider charge. A provider is not considered verified until that request returns a non-empty response.

## Safety boundary

The desktop chat sends plain text to the selected provider. It does **not** dispatch registered skills, shell commands, or workspace changes. Those capabilities remain behind the existing guarded runtime and are not exposed in this first desktop MVP.

Messages may leave the computer when a cloud provider is selected. Review the privacy prompt before chatting and do not enter passwords, API keys, or sensitive data. Local provider selection does not itself prove all data stays local; verify routing settings, especially when using hybrid mode.

The UI runs provider calls in a background thread to keep the window responsive. It does not yet provide hard cancellation of an in-flight provider request, streaming token output, native OS keychain integration, voice capture, signed installers, or automatic updates. Closing the window does not guarantee that an already-started remote request is cancelled.

## Data and onboarding state

Progress is stored at `$YUKI_HOME/desktop_onboarding.json` when `YUKI_HOME` is set; otherwise the default is under the user's home directory. The file stores setup-step names and the provider name only. It does not store API keys or conversation history. This MVP does not yet include a conversation-history persistence feature or memory-management screen.

## Tests and validation

Run the focused tests from the repository root:

```sh
python -m unittest tests.test_desktop_onboarding tests.test_desktop_service
```

Then run the repository's full test/CI suite. The app requires a Python build with Tk support; on Linux, the OS may package Tk separately. The desktop app remains an early MVP until the supported OS matrix, packaged-artifact tests, and independent security review are complete.
