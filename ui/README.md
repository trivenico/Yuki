# Yuki user interfaces

## Desktop chat

The first cross-platform desktop client uses Python's standard-library Tk UI and the authenticated local conversation API.

Requirements:
- Python 3.10+
- Tk support installed for the Python distribution
- Existing Yuki dependencies and provider configuration

Start the main Yuki CLI and run:

    /desktop

Or launch from Python:

    from yuki_providers import ProviderManager
    from ui.desktop import run_desktop
    run_desktop(ProviderManager())

On first use, set YUKI_BOOTSTRAP_ADMIN and YUKI_BOOTSTRAP_PASSWORD before launching. Passwords must meet the authentication service's minimum length. The API binds to loopback only. If a local API is already running on the configured port, the desktop client connects to it rather than shutting it down.

The desktop client keeps its bearer token in memory only. It does not persist credentials or tokens. Conversations are stored in the same SQLite database as the local API. The UI uses background threads for network operations so the window remains responsive during generation.

This is an MVP UI, not a packaged installer or a remote multi-user desktop deployment. Native app packaging, secure OS credential storage, accessibility, robust stream cancellation/reconnect, model selection, file attachments, voice, and cross-platform acceptance testing remain future work.
