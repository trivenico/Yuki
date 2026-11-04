@echo off
setlocal EnableExtensions
rem Timestamped backup of Yuki private workspace. No software is installed.
rem Usage: scripts\daily_backup.bat [source_dir] [backup_dir]
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
if not "%YUKI_HOME%"=="" (
  set "DATA_ROOT=%YUKI_HOME%"
) else (
  set "DATA_ROOT=%REPO_ROOT%"
)
if not "%~1"=="" (
  set "SOURCE_DIR=%~1"
) else if not "%YUKI_BACKUP_SOURCE%"=="" (
  set "SOURCE_DIR=%YUKI_BACKUP_SOURCE%"
) else if not "%YUKI_WORKSPACE%"=="" (
  if "%YUKI_WORKSPACE:~1,1%"==":" (
    set "SOURCE_DIR=%YUKI_WORKSPACE%"
  ) else if "%YUKI_WORKSPACE:~0,1%"=="\" (
    set "SOURCE_DIR=%YUKI_WORKSPACE%"
  ) else (
    set "SOURCE_DIR=%DATA_ROOT%\%YUKI_WORKSPACE%"
  )
) else (
  set "SOURCE_DIR=%DATA_ROOT%\yuki_workspace"
)
if not "%~2"=="" (
  set "BACKUP_DIR=%~2"
) else if not "%YUKI_BACKUP_DIR%"=="" (
  set "BACKUP_DIR=%YUKI_BACKUP_DIR%"
) else (
  set "BACKUP_DIR=%REPO_ROOT%\BACKUP\YukiData"
)
if "%YUKI_BACKUP_RETENTION_DAYS%"=="" (set "RETENTION_DAYS=14") else (set "RETENTION_DAYS=%YUKI_BACKUP_RETENTION_DAYS%")

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$ErrorActionPreference='Stop';" ^
 "$src=[IO.Path]::GetFullPath($env:SOURCE_DIR);" ^
 "$dst=[IO.Path]::GetFullPath($env:BACKUP_DIR);" ^
 "$days=14; if ($env:RETENTION_DAYS -notmatch '^\d+$' -or [int]$env:RETENTION_DAYS -lt 1 -or [int]$env:RETENTION_DAYS -gt 3650) { throw 'Retention must be a whole number from 1 to 3650 days.' }; $days=[int]$env:RETENTION_DAYS;" ^
 "if (!(Test-Path -LiteralPath $src -PathType Container)) { throw ('Backup source does not exist: ' + $src) };" ^
 "$srcRoot=(Resolve-Path -LiteralPath $src).Path.TrimEnd('\'); $src=$srcRoot + '\';" ^
 "New-Item -ItemType Directory -Force -Path $dst | Out-Null; $dst=(Resolve-Path -LiteralPath $dst).Path.TrimEnd('\');" ^
 "if ($dst.Equals($srcRoot,[StringComparison]::OrdinalIgnoreCase) -or $dst.StartsWith($src,[StringComparison]::OrdinalIgnoreCase)) { throw 'Backup destination must not be source or inside source.' };" ^
 "if ($srcRoot.StartsWith($dst.TrimEnd('\') + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Source must not be inside backup destination.' };" ^
 "$stamp=Get-Date -Format 'yyyyMMdd_HHmmss'; $archive=Join-Path $dst ('yuki-data-' + $stamp + '.zip'); $temp=$archive + '.tmp.zip';" ^
 "Add-Type -AssemblyName System.IO.Compression.FileSystem;" ^
 "try { [IO.Compression.ZipFile]::CreateFromDirectory($src, $temp, [IO.Compression.CompressionLevel]::Optimal, $false); $zip=[IO.Compression.ZipFile]::OpenRead($temp); try { if ($zip.Entries.Count -eq 0) { throw 'Archive is empty.' }; foreach ($entry in $zip.Entries) { $stream=$entry.Open(); try { $buffer=New-Object byte[] 8192; while ($stream.Read($buffer,0,$buffer.Length) -gt 0) { } } finally { $stream.Dispose() } } } finally { $zip.Dispose() }; Move-Item -LiteralPath $temp -Destination $archive; } finally { if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force } };" ^
 "Get-ChildItem -LiteralPath $dst -File -Filter 'yuki-data-*.zip' | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$days) } | Remove-Item -Force;" ^
 "Write-Output ('Backup completed: ' + $archive); Write-Output ('Retention: ' + $days + ' days. Keep the drive encrypted and store another copy separately.')"
if errorlevel 1 (
  echo Backup failed. Review the PowerShell error above.
  exit /b 1
)
endlocal
