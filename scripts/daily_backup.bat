@echo off
setlocal EnableExtensions
rem Timestamped backup of Yuki private data. No software is installed.
rem Usage: scripts\daily_backup.bat [source_dir] [backup_dir]
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
if "%~1"=="" (
  if not "%YUKI_HOME%"=="" (set "SOURCE_DIR=%YUKI_HOME%") else (set "SOURCE_DIR=%REPO_ROOT%\YukiData")
) else (
  set "SOURCE_DIR=%~1"
)
if "%~2"=="" (
  if not "%YUKI_BACKUP_DIR%"=="" (set "BACKUP_DIR=%YUKI_BACKUP_DIR%") else (set "BACKUP_DIR=%REPO_ROOT%\BACKUP\YukiData")
) else (
  set "BACKUP_DIR=%~2"
)
if "%YUKI_BACKUP_RETENTION_DAYS%"=="" (set "RETENTION_DAYS=14") else (set "RETENTION_DAYS=%YUKI_BACKUP_RETENTION_DAYS%")

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$ErrorActionPreference='Stop';" ^
 "$src=[IO.Path]::GetFullPath($env:SOURCE_DIR);" ^
 "$dst=[IO.Path]::GetFullPath($env:BACKUP_DIR);" ^
 "$days=14; if ($env:RETENTION_DAYS -notmatch '^\d+$' -or [int]$env:RETENTION_DAYS -lt 1 -or [int]$env:RETENTION_DAYS -gt 3650) { throw 'Retention must be a whole number from 1 to 3650 days.' }; $days=[int]$env:RETENTION_DAYS;" ^
 "if (!(Test-Path -LiteralPath $src -PathType Container)) { throw ('Backup source does not exist: ' + $src) };" ^
 "$src=(Resolve-Path -LiteralPath $src).Path.TrimEnd('\') + '\';" ^
 "if ($dst.StartsWith($src,[StringComparison]::OrdinalIgnoreCase)) { throw 'Backup destination must not be inside source.' };" ^
 "if ($src.StartsWith($dst.TrimEnd('\') + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Source must not be inside backup destination.' };" ^
 "New-Item -ItemType Directory -Force -Path $dst | Out-Null;" ^
 "$stamp=Get-Date -Format 'yyyyMMdd_HHmmss'; $archive=Join-Path $dst ('yuki-data-' + $stamp + '.zip'); $temp=$archive + '.tmp.zip';" ^
 "try { Compress-Archive -Path (Join-Path $src '*') -DestinationPath $temp -CompressionLevel Optimal; if (!(Test-Path -LiteralPath $temp) -or (Get-Item -LiteralPath $temp).Length -eq 0) { throw 'Archive was not created or is empty.' }; Move-Item -LiteralPath $temp -Destination $archive; } finally { if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force } };" ^
 "Get-ChildItem -LiteralPath $dst -File -Filter 'yuki-data-*.zip' | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$days) } | Remove-Item -Force;" ^
 "Write-Output ('Backup completed: ' + $archive); Write-Output ('Retention: ' + $days + ' days. Keep the drive encrypted and store another copy separately.')"
if errorlevel 1 (
  echo Backup failed. Review the PowerShell error above.
  exit /b 1
)
endlocal
