# Yuki on an external SSD

## What portability means

Yuki's source and local data can travel on an external SSD. The current project is Python-based and is **not yet a fully self-contained, zero-install portable application**. The host computer still needs Python 3.10 or newer and the selected dependencies. Optional capabilities such as local LLMs, speech models, Playwright/Chromium, Nmap, Docker, and camera access have separate platform and hardware requirements.

## Quick start launchers

From the repository on the SSD:

- Windows: run `scripts/portable-yuki-windows.bat`
- Linux/macOS: run `sh scripts/portable-yuki-unix.sh`

The launchers select the repository's virtual environment when present, otherwise use a host Python 3.10+ runtime. They verify the Python version and core YAML dependency, then run a preflight that creates and tests the configured `YUKI_HOME` with a temporary write probe. Startup stops if the data volume is missing, a symbolic link, or not writable. They do not install software or elevate privileges. Both launchers preserve an explicitly supplied `YUKI_HOME`, allowing the private data directory to live on another encrypted volume. Install core dependencies on a trusted machine first:

```sh
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

A virtual environment is OS- and Python-version-specific; do not expect a Windows `.venv` to run on Linux or vice versa. For the first practical version, keep source on the SSD and create a separate virtual environment on each trusted host, or prepare one SSD folder per operating system.

## Portable data location

The runtime now supports:

- `YUKI_HOME`: root for relative workspace data. If not set, the repository root is used.
- `YUKI_CONFIG`: optional config file path. If not set, the repository's `config.yaml` is used.
- `workspace` in the YAML config: relative workspace paths resolve under `YUKI_HOME` when configured; absolute paths remain absolute.

Both launchers default `YUKI_HOME` to `YukiData` beside the repository only when the caller has not already set it. The preflight verifies that this data directory can actually accept writes before starting the application; it does not claim to encrypt the drive or replace OS volume encryption. Put private memory, health/habit records, reminders, evolution reports, downloaded models, and tokens only in this data area or another deliberate private location. The data directory is ignored by Git.

## Recommended SSD layout

```text
Yuki-SSD/
  Yuki/                 # versioned source code (Git repository)
  YukiData/             # private data; not committed
    yuki_workspace/
    models/             # optional, potentially very large
  BACKUP/               # encrypted backups, disconnected when not in use
```

The launcher scripts are starter launchers, not a portable Python runtime and not a packaged app. They call a compatible Python executable available on the host or in the repository's OS-specific virtual environment. Do not place the only copy of your data on one SSD.

## Security checklist

1. **Encrypt the whole drive** with a reputable full-disk/volume encryption tool before storing personal data. Keep the recovery key somewhere separate and safe.
2. Do not save provider keys, OAuth refresh tokens, browser sessions or passwords in Git, `config.yaml`, or plaintext notes. Prefer environment variables supplied only on a trusted computer; clear them when finished.
3. Do not leave credentials on shared/public computers. If a machine is untrusted, use Yuki only in a restricted mode and do not connect accounts.
4. Safely eject the drive after Yuki has stopped and files are closed. Abrupt removal can corrupt the local JSON stores.
5. Keep a versioned backup of `YukiData`, ideally encrypted and stored separately. Test restoring it.
6. Local AI is offline only if the model and compatible runtime are installed locally. Cloud models, remote vision/image generation, messaging and web features require network access and may send selected content to external providers.
7. Never enable automatic arbitrary shell execution merely to make the portable agent feel more autonomous.

## Portability acceptance checklist

Before describing a release as portable, CI should verify path resolution on Windows and Linux, launch smoke tests, workspace persistence when the drive path contains spaces, backup/restore, and safe behavior when the SSD is absent or read-only. These launcher preflight checks improve version and path handling; they do not constitute the complete cross-platform test matrix or a self-contained portable release.


## Portable runtime integrity manifests

The repository includes `scripts/portable_runtime_manifest.py` for packaging
pipelines. After assembling a platform-specific runtime directory, create and
verify its exact file inventory:

```sh
python scripts/portable_runtime_manifest.py create ./YukiRuntime --platform windows-x86_64 --runtime-version 3.12.7
python scripts/portable_runtime_manifest.py verify ./YukiRuntime
```

The manifest records every regular payload file's relative path, byte size,
and SHA-256 digest. Verification rejects modified, missing, unexpected,
non-regular, symbolic-link, duplicate, or path-traversing entries. The
manifest is explicitly **unsigned**: it detects changes relative to the
manifest but does not authenticate the publisher. Production distribution
must additionally sign the manifest or package and verify that signature
against a trusted release key before execution.
