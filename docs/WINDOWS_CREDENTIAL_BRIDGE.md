# Windows Credential Manager integration

Yuki's optional .NET 8 bridge is in
`integrations/dotnet/Yuki.WindowsCredentialBridge`. It implements the
existing `core.oauth_credential_vault.CredentialCipher` interface through an
explicit Python adapter at `core/windows_credential_cipher.py`.

The Windows helper stores a DPAPI-protected AES-256-GCM key in the current
user's Windows Credential Manager. The existing Python vault continues to
own SQLite metadata, owner/provider/account isolation, token refresh version
fencing, expiry, scopes and audit. Its associated data remains bound to
owner, provider, account and token kind. The HMAC key remains a separate
host-managed secret.

Secrets cross the Python/.NET process boundary only through stdin/stdout pipes;
they are not command-line arguments, environment variables, disk files or
logs. The executable is configured by an absolute trusted deployment path and
is never auto-discovered. Non-Windows systems fail explicitly.

See [the .NET deployment and recovery guide](../integrations/dotnet/README.md)
for build instructions, threat model, key recovery limitations and rotation
requirements.
