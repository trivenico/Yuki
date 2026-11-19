# Phase 98 — First-use desktop readiness

## What this milestone fixes

- The desktop launcher imports `pathlib.Path` at module scope so authenticated session composition can resolve the configured workspace.
- A fresh desktop profile can bootstrap its first local administrator exactly once using environment variables. The existing `BuiltInAuthService.bootstrap_admin` method refuses to create another initial administrator after any account exists.
- If the profile has no account and bootstrap credentials are not configured, the login screen explains the required setup rather than presenting an unusable sign-in form.

## Developer setup

Use Python 3.10 or newer.

Install the core and desktop dependencies:

```sh
python -m pip install -r requirements.txt
python -m pip install -r requirements-desktop.txt
```

The default provider is OpenAI-compatible and uses the `gpt-4.1-mini` model setting. Set the API key in the process environment; do not put secrets in `config.yaml`.

### macOS / Linux

```sh
export OPENAI_API_KEY='your-api-key'
export YUKI_BOOTSTRAP_ADMIN_USERNAME='yuki-admin'
export YUKI_BOOTSTRAP_ADMIN_PASSWORD='a-long-unique-password-at-least-12-characters'
python -m ui.desktop.launch
```

### Windows PowerShell

```powershell
$env:OPENAI_API_KEY = 'your-api-key'
$env:YUKI_BOOTSTRAP_ADMIN_USERNAME = 'yuki-admin'
$env:YUKI_BOOTSTRAP_ADMIN_PASSWORD = 'a-long-unique-password-at-least-12-characters'
python -m ui.desktop.launch
```

On first launch, the bootstrap variables create the initial administrator only when the local account database is empty. Sign in using those credentials. Once the account exists, the bootstrap variables are no longer used to create accounts; account creation and role management must follow the authenticated administration path.

The desktop UI currently requires an explicit local account and a configured provider. The OpenAI-compatible provider reports unavailable when `OPENAI_API_KEY` is missing. Yuki does not silently switch to a mock provider or send prompts to a different cloud service.

## Current limitations

- This is a developer setup path, not a signed consumer installer.
- PySide6 and native Qt dependencies must be available on the target machine.
- The default provider requires a valid API key and network access. Local Ollama is supported when configured with an installed model and running local service.
- Voice, continuous wake-word listening, cross-platform desktop observation, and full 100-agent execution are not prerequisites for this first-use milestone and are not represented as production-ready.
- The desktop conversation and agent paths still require automated integration and packaging smoke tests on supported operating systems before a release can be called verified.

## Verification status

The code changes are committed to the open draft PR. No passing CI result or local test execution is claimed for this commit until the corresponding checks are observed.
