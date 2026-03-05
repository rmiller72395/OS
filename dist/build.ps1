# dist/build.ps1 — Produce one-shot zip for Sovereign v5.0 rollout
# Run from repo root: .\dist\build.ps1
# Output: dist/out/ (contents) and dist/sovereign_v5.0_rollout.zip

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$distName = "sovereign_v5.0_rollout"
$outDir = Join-Path (Join-Path $root "dist") "out"
$zipPath = Join-Path (Join-Path $root "dist") "${distName}.zip"

if (Test-Path $outDir) { Remove-Item -Recurse -Force $outDir }
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

$include = @(
    "bot.py",
    "execution.py",
    "execution_models.py",
    "config_schema.py",
    "requirements.txt",
    "README.md",
    "VERSION",
    ".env.example",
    "run_windows.ps1",
    "verify_execution_layer.py",
    "CEO_MASTER_SOUL_v3.md",
    "GOVERNANCE_AND_VISION.md",
    "TECHNICAL_MANIFESTO.md",
    "EXECUTION_LAYER_SPEC.md",
    "observability",
    "tickets",
    "dashboard",
    "notifications",
    "sovereign",
    "skills",
    "tests",
    "data",
    "sovereign_config.template.json",
    "RELEASE_CHECKLIST_v5.0.md"
)

foreach ($item in $include) {
    $src = Join-Path $root $item
    $dst = Join-Path $outDir $item
    if (Test-Path $src) {
        if (Test-Path $dst) { Remove-Item $dst -Recurse -Force -ErrorAction SilentlyContinue }
        Copy-Item -Path $src -Destination $dst -Recurse -Force
    }
}

# start.ps1 — Task Scheduler friendly (same as run_windows.ps1 if not already present)
$startPs1 = Join-Path $outDir "start.ps1"
if (-not (Test-Path $startPs1)) {
    Set-Content -Path $startPs1 -Value @'
# Sovereign v5.0 — Task Scheduler friendly launcher
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
if (Test-Path (Join-Path $here '.env')) {
    Get-Content (Join-Path $here '.env') | ForEach-Object {
        $line = $_.Trim()
        if ($line.Length -eq 0 -or $line.StartsWith('#')) { return }
        $parts = $line.Split('=',2)
        if ($parts.Count -eq 2 -and $parts[0].Trim()) {
            Set-Item -Path "Env:$($parts[0].Trim())" -Value $parts[1]
        }
    }
}
python bot.py
'@ -Encoding UTF8
}

# run_windows.bat — for Task Scheduler / double-click
$bat = @"
@echo off
cd /d "%~dp0"
if exist .env for /f "usebackq tokens=*" %%a in (".env") do set "%%a"
python bot.py
pause
"@
$batPath = Join-Path $outDir "run_windows.bat"
Set-Content -Path $batPath -Value $bat -Encoding ASCII

# switch_release.ps1 — switch to this release (e.g. after unzip elsewhere)
$switchPs1 = @'
# Usage: .\switch_release.ps1 <target_dir>
# Copies or points to this release in target_dir (e.g. C:\Sovereign\releases\v5.0)
param([Parameter(Mandatory=$true)] [string]$TargetDir)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path $TargetDir)) { New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null }
Copy-Item -Path (Join-Path $here "*") -Destination $TargetDir -Recurse -Force
Write-Host "Release files copied to $TargetDir"
'@
Set-Content -Path (Join-Path $outDir "switch_release.ps1") -Value $switchPs1 -Encoding UTF8

# rollback.ps1 — restore from backup (reminder)
$rollbackPs1 = @'
# Rollback: stop the bot, then restore your previous release folder from backup.
# Example: copy backup\sovereign_v4.10_rollout\* . 
# Then: python -m sovereign self-test ; python bot.py
Write-Host "1. Stop the bot (Discord /stop or kill process)"
Write-Host "2. Restore previous release folder from backup"
Write-Host "3. Run: python -m sovereign self-test"
Write-Host "4. Run: python bot.py or .\start.ps1"
'@
Set-Content -Path (Join-Path $outDir "rollback.ps1") -Value $rollbackPs1 -Encoding UTF8

# Create zip
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path (Join-Path $outDir "*") -DestinationPath $zipPath -Force
Write-Host "Created: $zipPath"
Write-Host "Contents: $outDir"
