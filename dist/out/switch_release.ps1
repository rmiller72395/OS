# Usage: .\switch_release.ps1 <target_dir>
# Copies or points to this release in target_dir (e.g. C:\Sovereign\releases\v5.0)
param([Parameter(Mandatory=$true)] [string]$TargetDir)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path $TargetDir)) { New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null }
Copy-Item -Path (Join-Path $here "*") -Destination $TargetDir -Recurse -Force
Write-Host "Release files copied to $TargetDir"
