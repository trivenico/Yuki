$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { throw "Python 3.11+ is required. Install Python and enable the Python launcher." }
if (-not $env:YUKI_VENV_DIR) { $env:YUKI_VENV_DIR = Join-Path $Root ".venv" }
python scripts/install_local.py --venv-dir $env:YUKI_VENV_DIR
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$VenvPython = Join-Path $env:YUKI_VENV_DIR "Scripts/python.exe"
& $VenvPython scripts/launch_local.py @args
exit $LASTEXITCODE
