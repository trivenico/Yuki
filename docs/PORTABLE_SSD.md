# Yuki on an external SSD

## What portability means

Yuki's source and local data can travel on an external SSD. The current project is Python-based and is **not yet a fully self-contained, zero-install portable application**. The host computer still needs a compatible Python runtime and the selected dependencies. Optional capabilities such as local LLMs, speech models, Playwright/Chromium, Nmap, Docker, and camera access have separate platform and hardware requirements.

## Quick start launchers

From the repository on the SSD:

- Windows: run `scripts/portable-yuki-windows.bat`
- Linux/macOS: run `sh scripts/portable-yuki-unix.sh`

The launchers check for Python and the core YAML dependency; they do not install software or elevate privileges. Install core dependencies on a trusted machine first:

```sh
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

Use the Python interpreter from that virtual environment when starting Yuki. A virtual environment is OS- and Python-version-specific; do not expect a Windows `.venv` to run on Linux or vice versa. For the first practical version, keep source on the SSD and create a separate virtual environment on each trusted host, or prepare one SSD folder per operating system.

## Portable data location

The runtime now supports:

- `YUKI_HOME`: root for relative workspace data. If not set, the repository root is used.
- `YUKI_CONFIG`: optional config file path. If not set, the repository's `config.yaml` is used.
- `workspace` in the YAML config: relative workspace paths resolve under `YUKI_HOME` when configured; absolute paths remain absolute.

The Windows launcher defaults `YUKI_HOME` to `YukiData` beside the repository. The Unix launcher uses `YukiData` beside the repository unless `YUKI_HOME` is already set. Put private memory, health/habit records, reminders, evolution reports, downloaded models, and tokens only in this data area or another deliberate private location. The data directory is ignored by Git.

## Recommended SSD layout

```text
Yuki-SSD/
  Yuki/                 # versioned source code (Git repository)
  YukiData/             # private data; not committed
    yuki_workspace/
    models/             # optional, potentially very large
  BACKUP/               # encrypted backups, disconnected when not in use
```

The launcher scripts are starter launchers, not a portable Python runtime and not a packaged app. They currently call the Python executable available on the host. Do not place the only copy of your data on one SSD.

## Security checklist

1. **Encrypt the whole drive** with a reputable full-disk/volume encryption tool before storing personal data. Keep the recovery key somewhere separate and safe.
2. Do not save provider keys, OAuth refresh tokens, browser sessions or passwords in Git, `config.yaml`, or plaintext notes. Prefer environment variables supplied only on a trusted computer; clear them when finished.
3. Do not leave credentials on shared/public computers. If a machine is untrusted, use Yuki only in a restricted mode and do not connect accounts.
4. Safely eject the drive after Yuki has stopped and files are closed. Abrupt removal can corrupt the local JSON stores.
5. Keep a versioned backup of `YukiData`, ideally encrypted and stored separately. Test restoring it.
6. Local AI is offline only if the model and compatible runtime are installed locally. Cloud models, remote vision/image generation, messaging and web features require network access and may send selected content to external providers.
7. Never enable automatic arbitrary shell execution merely to make the portable agent feel more autonomous.

## Portability acceptance checklist

Before describing a release as portable, CI should verify path resolution on Windows and Linux, launch smoke tests, workspace persistence when the drive path contains spaces, backup/restore, and safe behavior when the SSD is absent or read-only. This commit adds path-resolution tests and launch scripts; it does not yet provide that complete cross-platform test matrix.
