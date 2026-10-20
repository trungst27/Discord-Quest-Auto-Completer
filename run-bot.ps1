$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$mutex = New-Object System.Threading.Mutex($false, 'Local\DiscordBotKeep')
if (-not $mutex.WaitOne(0)) { exit 0 }

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$logDir = Join-Path $PSScriptRoot 'logs'
$supervisorLog = Join-Path $logDir 'supervisor.log'

try {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null

    if (-not (Test-Path -LiteralPath $python)) {
        "Python venv missing at $python" | Add-Content -Path $supervisorLog
        exit 1
    }

    if (-not $env:DISCORD_TOKEN) {
        $tokenFile = Join-Path $PSScriptRoot '.bot-token.xml'
        if (-not (Test-Path -LiteralPath $tokenFile)) {
            "No DISCORD_TOKEN and no .bot-token.xml. Run setup-bot.ps1." | Add-Content -Path $supervisorLog
            exit 1
        }
        $credential = Import-Clixml -LiteralPath $tokenFile
        $env:DISCORD_TOKEN = $credential.GetNetworkCredential().Password
    }

    # Crash loop guard: back off instead of hammering Discord every 30s.
    $backoff = 30
    while ($true) {
        $started = Get-Date
        "--- $(Get-Date -Format s) starting bot.py ---" | Add-Content -Path $supervisorLog
        try {
            & $python (Join-Path $PSScriptRoot 'bot.py')
            $code = $LASTEXITCODE
        }
        catch {
            "bot.py threw: $_" | Add-Content -Path $supervisorLog
            $code = 1
        }

        $ran = ((Get-Date) - $started).TotalSeconds
        "--- exited code $code after $([math]::Round($ran))s ---" | Add-Content -Path $supervisorLog

        # Clean long run resets the backoff; a fast crash doubles it up to 10 min.
        if ($ran -gt 300) { $backoff = 30 } else { $backoff = [math]::Min($backoff * 2, 600) }

        Start-Sleep -Seconds $backoff
    }
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
