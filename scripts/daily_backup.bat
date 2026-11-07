@echo off
setlocal EnableExtensions
rem Timestamped backup of Yuki private workspace.
rem Usage: scripts\daily_backup.bat [source_dir] [backup_dir]
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "ARG_SOURCE=%~1"
set "ARG_DESTINATION=%~2"
if "%YUKI_BACKUP_RETENTION_DAYS%"=="" set "YUKI_BACKUP_RETENTION_DAYS=14"
if "%YUKI_PYTHON%"=="" set "YUKI_PYTHON=python"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$ErrorActionPreference='Stop';" ^
 "$root=[IO.Path]::GetFullPath($env:REPO_ROOT);" ^
 "$dataRoot=if ($env:YUKI_HOME) { if ([IO.Path]::IsPathRooted($env:YUKI_HOME)) {[IO.Path]::GetFullPath($env:YUKI_HOME)} else {[IO.Path]::GetFullPath((Join-Path $root $env:YUKI_HOME))} } else {$root};" ^
 "if ($env:ARG_SOURCE) {$srcInput=$env:ARG_SOURCE; $srcBase=$root} elseif ($env:YUKI_BACKUP_SOURCE) {$srcInput=$env:YUKI_BACKUP_SOURCE; $srcBase=$root} elseif ($env:YUKI_WORKSPACE) {$srcInput=$env:YUKI_WORKSPACE; $srcBase=$dataRoot} else {$srcInput='yuki_workspace'; $srcBase=$dataRoot};" ^
 "$src=if ([IO.Path]::IsPathRooted($srcInput)) {[IO.Path]::GetFullPath($srcInput)} else {[IO.Path]::GetFullPath((Join-Path $srcBase $srcInput))};" ^
 "$dstInput=if ($env:ARG_DESTINATION) {$env:ARG_DESTINATION} elseif ($env:YUKI_BACKUP_DIR) {$env:YUKI_BACKUP_DIR} else {'BACKUP\YukiData'};" ^
 "$dst=if ([IO.Path]::IsPathRooted($dstInput)) {[IO.Path]::GetFullPath($dstInput)} else {[IO.Path]::GetFullPath((Join-Path $root $dstInput))};" ^
 "$days=0; if ($env:YUKI_BACKUP_RETENTION_DAYS -notmatch '^\d+$' -or ![int]::TryParse($env:YUKI_BACKUP_RETENTION_DAYS,[ref]$days) -or $days -lt 1 -or $days -gt 3650) { throw 'Retention must be a whole number from 1 to 3650 days.' };" ^
 "if (!(Test-Path -LiteralPath $src -PathType Container)) { throw ('Backup source does not exist: ' + $src) };" ^
 "$srcRoot=(Resolve-Path -LiteralPath $src).Path.TrimEnd('\'); $src=$srcRoot + '\';" ^
 "New-Item -ItemType Directory -Force -Path $dst | Out-Null; $dst=(Resolve-Path -LiteralPath $dst).Path.TrimEnd('\');" ^
 "if ($dst.Equals($srcRoot,[StringComparison]::OrdinalIgnoreCase) -or $dst.StartsWith($src,[StringComparison]::OrdinalIgnoreCase)) { throw 'Backup destination must not be source or inside source.' };" ^
 "if ($srcRoot.StartsWith($dst.TrimEnd('\') + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Source must not be inside backup destination.' };" ^
 "$stamp=Get-Date -Format 'yyyyMMdd_HHmmss'; $stage=Join-Path $dst ('.yuki-stage-' + [guid]::NewGuid().ToString('N')); $snapshot=Join-Path $stage (Split-Path $srcRoot -Leaf); $temp=Join-Path $stage 'backup.tmp.zip'; $extension=if ($env:YUKI_BACKUP_KEY_FILE) {'.yukibak'} else {'.zip'}; $archive=Join-Path $dst ('yuki-data-' + $stamp + $extension);" ^
 "Add-Type -AssemblyName System.IO.Compression.FileSystem;" ^
 "try { New-Item -ItemType Directory -Path $stage | Out-Null; $python=$env:YUKI_PYTHON; & $python (Join-Path $env:SCRIPT_DIR 'sqlite_workspace_snapshot.py') $src $snapshot; if ($LASTEXITCODE -ne 0) { throw 'SQLite-aware workspace snapshot failed.' }; [IO.Compression.ZipFile]::CreateFromDirectory($snapshot, $temp, [IO.Compression.CompressionLevel]::Optimal, $false); $zip=[IO.Compression.ZipFile]::OpenRead($temp); try { if ($zip.Entries.Count -eq 0) { throw 'Archive is empty.' }; foreach ($entry in $zip.Entries) { $stream=$entry.Open(); try { $buffer=New-Object byte[] 8192; while ($stream.Read($buffer,0,$buffer.Length) -gt 0) { } } finally { $stream.Dispose() } } } finally { $zip.Dispose() }; if ($env:YUKI_BACKUP_KEY_FILE) { & $python (Join-Path $env:SCRIPT_DIR 'backup_crypto.py') encrypt $temp $archive --key-file $env:YUKI_BACKUP_KEY_FILE; if ($LASTEXITCODE -ne 0) { throw 'Authenticated backup encryption failed.' } } else { Move-Item -LiteralPath $temp -Destination $archive }; } finally { if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }; if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force } };" ^
 "Get-ChildItem -LiteralPath $dst -File | Where-Object { ($_.Name -like 'yuki-data-*.zip' -or $_.Name -like 'yuki-data-*.yukibak') -and $_.LastWriteTime -lt (Get-Date).AddDays(-$days) } | Remove-Item -Force;" ^
 "Write-Output ('Backup completed: ' + $archive); if ($env:YUKI_BACKUP_KEY_FILE) { Write-Output 'Archive authenticated and encrypted with AES-256-GCM.' } else { Write-Output 'WARNING: archive is plaintext. Set YUKI_BACKUP_KEY_FILE to enable authenticated encryption.' }"
if errorlevel 1 exit /b 1
endlocal
