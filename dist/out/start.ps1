# Sovereign v5.0 â€” Task Scheduler friendly launcher
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
