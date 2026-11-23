# Yuki Windows credential bridge

The .NET 8 helper implements the existing Python `CredentialCipher` contract.
It is an optional Windows-only adapter and does not replace Yuki's owner-scoped
SQLite vault, OAuth lifecycle, token validation, refresh fencing or audit.

## Cryptographic design

- A random 256-bit data-encryption key is created once per Windows user.
- The key is protected with Windows DPAPI in current-user scope and stored as
  a Generic Credential in Windows Credential Manager.
- Token encryption uses AES-256-GCM with a fresh 96-bit nonce and 128-bit tag.
- Existing Python vault associated data binds ciphertext to owner, provider,
  account and token kind.
- The bridge returns a versioned key identifier; the Python vault stores it
  with ciphertext and uses it to select the decrypt key.
- DPAPI/native temporary buffers are cleared where managed/native APIs permit.

The Python adapter sends base64-encoded request data over the child process's
stdin pipe and reads the response from stdout. Secrets are never command-line
arguments, environment variables, files, logs or diagnostic events. Configure
the bridge using an absolute path to a trusted self-contained executable. Do
not allow user-uploaded paths or model-controlled executable selection.

The Python vault still requires a separate, stable HMAC key of at least 32
bytes. It must be managed independently from the DPAPI-protected AES key.

## Build

On Windows with .NET 8 SDK:

```powershell
dotnet test integrations/dotnet/Yuki.WindowsCredentialBridge.Tests/Yuki.WindowsCredentialBridge.Tests.csproj -c Release
dotnet publish integrations/dotnet/Yuki.WindowsCredentialBridge/Yuki.WindowsCredentialBridge.csproj -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -o artifacts/windows-credential-bridge
```

Python host composition:

```python
from core.oauth_credential_vault import SQLiteOAuthCredentialVault
from core.windows_credential_cipher import WindowsCredentialCipher

cipher = WindowsCredentialCipher(
    r"C:\Program Files\Yuki\Yuki.WindowsCredentialBridge.exe"
)
vault = SQLiteOAuthCredentialVault(
    database_path,
    cipher=cipher,
    hmac_key=separately_managed_hmac_key,
)
```

The executable path is a deployment trust decision. The bridge is not
auto-discovered, and non-Windows platforms fail explicitly rather than falling
back to plaintext.

## Threat model and limitations

- Credential Manager and DPAPI protect key material at rest for the current
  Windows user; malware running as that user may still request decryption.
- This does not defend against a compromised logged-in user session, debugger,
  process-memory dump, administrator, or kernel compromise.
- DPAPI keys are user/profile and machine context bound. Profile migration,
  machine replacement or profile corruption may make stored ciphertext
  unrecoverable. Back up and restore the protected user profile using an
  approved Windows recovery process; never export plaintext keys.
- The current key ID is `wincred-v1`. Rotation requires a separately reviewed
  migration that decrypts and re-encrypts existing vault records. Replacing or
  deleting the current Credential Manager key without migrating records will
  make those records unavailable.
- No plaintext fallback exists. Missing, corrupt or inaccessible keys fail
  closed.
- The helper supports only the current Windows user context; service accounts
  must run under a dedicated, managed Windows identity with a persistent
  profile and explicit Credential Manager provisioning.

The native bridge and Windows integration tests must pass before production
enablement. The adapter remains optional and is not automatically selected by
Yuki's default configuration.
