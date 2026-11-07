$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { throw "Python 3.10+ is required. Install Python and enable the Python launcher." }
python scripts/install_local.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$Root\.venv\Scripts\python.exe" scripts/launch_local.py @args
exit $LASTEXITCODE
