# User interface

Yuki currently includes a browser chat interface in \`ui/web\` and a guarded terminal entry point in \`main.py\`.

## Browser interface

The browser UI provides authenticated sign-in, conversations, provider selection for administrators, OAuth integration management, and an owner-facing local-call invitation panel.

The local-call panel:
- Loads active devices belonging to the authenticated owner.
- Requires distinct caller and recipient device selections.
- Displays the exact device names and IDs in a confirmation prompt.
- Re-authenticates the active owner session with the account password.
- Requests a short-lived challenge bound to that owner session and exact device pair.
- Immediately consumes the one-time challenge to create an invitation.
- Clears the password field after the operation.
- States clearly that the recipient must separately accept and that invitation creation does not activate a microphone or camera.

The panel creates invitation state only. Incoming-call acceptance is currently exposed through the paired-device API; the browser panel does not impersonate a paired device or accept calls on its behalf. There is no WebRTC media transport or push notification implementation yet.

## Security requirements

- Serve the UI and API over HTTPS outside a strictly local development environment.
- Keep owner session tokens out of URLs and logs.
- Never pass passwords or confirmation tokens to models, agents, workers, analytics, or third-party scripts.
- Preserve server-side session validation, device ownership checks, challenge expiry and one-time consumption.
- Do not treat a browser confirmation prompt as a substitute for server-side authorization.
- Future UI changes must not bypass \`core.permissions.PermissionGate\` for task execution.

## Terminal interface

The terminal entry point exposes status, registered skills, and a one-action approval prompt. It is not a substitute for the browser owner-confirmation flow.
