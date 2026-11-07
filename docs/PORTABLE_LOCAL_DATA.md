# Portable local data directory

Yuki supports keeping runtime data outside the source checkout through `YUKI_HOME`.
This is useful for a dedicated data disk or external SSD, but it does not make a
Python virtual environment or native model runtime portable between operating
systems or CPU architectures.

## Windows PowerShell

Choose a stable data directory before the first launch:

```powershell
$env:YUKI_HOME = "E:\YukiData"
$env:YUKI_VENV_DIR = "E:\YukiRuntime\venv"
.\scripts\start_yuki.ps1
```

## macOS / Linux

```bash
export YUKI_HOME="/Volumes/YukiData"
export YUKI_VENV_DIR="/Volumes/YukiRuntime/venv"
./scripts/start_yuki.sh
```

The repository's configuration file remains the default unless `YUKI_CONFIG`
is set. Relative workspace paths in that configuration are resolved beneath
`YUKI_HOME`; absolute workspace paths remain absolute.

The browser runtime stores its SQLite databases (conversations, authentication,
memory, and owner profile) under the configured workspace. Back up the whole
workspace while Yuki is stopped. Do not copy a live SQLite database file by
ordinary file copy while writes may be in progress.

## Moving between computers

1. Stop Yuki cleanly.
2. Make a verified backup of the entire workspace.
3. Keep API keys and passwords out of the backup unless stored in an encrypted
   secret manager; Yuki expects provider secrets to be supplied separately.
4. Restore the workspace to the new machine and set `YUKI_HOME` to its parent
   data directory, preserving the configured relative workspace path.
5. Recreate the Python environment on the destination machine with that
   machine's Python interpreter. Do not move a `.venv` between operating
   systems, architectures, or Python installations.
6. Run the readiness checks and validate login, conversations, and memory
   before deleting the old copy.

## Current limitations

- This is configurable data placement, not a fully relocatable packaged app.
- There is not yet an automated backup/restore command or verified restore drill.
- External-drive removal while Yuki is running can cause database I/O failures;
  stop Yuki before disconnecting the drive.
- Absolute paths in configuration remain tied to the original machine.
- Provider models, Ollama installation, and OS credential stores are managed
  separately and are not copied with the workspace.
- A successful path-resolution test is not evidence of a successful cross-PC
  migration. Validate a real backup and restore before relying on portability.
