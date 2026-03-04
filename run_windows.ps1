# RMFramework v4.10 — Windows launcher
# Loads .env (if present) into process env vars, then runs bot.py.

$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$envFile = Join-Path $here '.env'
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line.Length -eq 0) { return }
    if ($line.StartsWith('#')) { return }
    $parts = $line.Split('=',2)
    if ($parts.Count -ne 2) { return }
    $k = $parts[0].Trim()
    $v = $parts[1]
    if ($k) { Set-Item -Path "Env:$k" -Value $v }
  }
  Write-Host "Loaded .env" -ForegroundColor Green
} else {
  Write-Host "No .env found (optional)." -ForegroundColor Yellow
}

python bot.py
