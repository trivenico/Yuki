# Portable dependency profiles

Yuki's base installation intentionally remains small. Install only the optional
profiles needed by the selected portable build; do not combine every profile
into a single oversized bundle by default.

| Profile | Install extra | Import-name coverage / notes |
|---|---|---|
| Core | base project | PyYAML is the only declared base third-party runtime dependency. |
| Voice | `[voice]` | faster-whisper, NumPy, sounddevice, soundfile, openwakeword, pyttsx3. |
| Desktop UI | `[ui]` | Textual terminal dashboard. A future native desktop shell may need a separate profile. |
| Email | `[email]` | Google API client, Google Auth and OAuth helper. |
| Browser automation | `[browser]` | Playwright Python package; browser engines are separate platform-specific downloads and must be pinned/packaged independently. |
| Defense diagnostics | `[defense]` | Scapy; packet capture may require OS-specific privileges/drivers and is not enabled by installation. |
| Vision / hosted image adapters | `[vision]` | OpenCV headless and OpenAI Python SDK. Camera permissions and external API credentials remain explicit. |
| Production storage | `[production-storage]` | PostgreSQL driver/pool, cryptography, OS keyring integration. |
| Trusted bootstrap | `[portable-bootstrap]` | Cryptography for Ed25519 verification. The host bootstrap must still be independently installed and trusted. |

Examples:

```bash
python -m pip install ".[voice]"
python -m pip install ".[browser,email]"
python -m pip install ".[production-storage,portable-bootstrap]"
```

## Build and release requirements

These extras are dependency declarations, not a complete lock or a guarantee
that every feature is portable. For each supported OS/architecture/Python
combination, the release builder must resolve and pin transitive dependencies,
download wheels into an offline wheelhouse, record SHA-256 hashes, and install
with `--no-index --require-hashes`. Build separate artifacts per platform.

Additional artifacts are outside Python extras:

- Playwright browser binaries and their OS shared-library dependencies.
- Piper executable and voice model files.
- Ollama or another local model server and model weights.
- Nmap, Docker, packet-capture drivers, OS frameworks, and camera/audio device access.
- Model/data licenses, notices, and provenance records.

Never bundle API credentials, OAuth tokens, signing private keys, or user data.
A profile being installed must not activate microphones, cameras, packet capture,
browser automation, or external network access by itself.

The static AST import inventory is an aid for finding source-level imports.
Review dynamic imports and feature boundaries manually, and validate each final
artifact on a clean machine with no host Python installation.
