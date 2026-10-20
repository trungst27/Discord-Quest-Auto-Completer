$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$reqFile = Join-Path $PSScriptRoot 'requirements-bot.txt'
$tokenFile = Join-Path $PSScriptRoot '.bot-token.xml'
$logDir = Join-Path $PSScriptRoot 'logs'
$taskName = 'DiscordBotKeep'

Write-Host '=== DiscordBotKeep setup ===' -ForegroundColor Cyan

# 1. venv
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment missing at $python. Create it with: python -m venv .venv"
}
Write-Host "[1/5] venv ok" -ForegroundColor Green

# 2. deps
& $python -m pip install --quiet --disable-pip-version-check -r $reqFile
if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }
Write-Host '[2/5] dependencies installed' -ForegroundColor Green

# 3. logs + env file
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.env'))) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.env.example') -Destination (Join-Path $PSScriptRoot '.env')
    Write-Host '[3/5] .env created from .env.example' -ForegroundColor Green
} else {
    Write-Host '[3/5] .env already present' -ForegroundColor Green
}

# 4. token — DPAPI-encrypted, readable only by this Windows user
if (Test-Path -LiteralPath $tokenFile) {
    $reuse = Read-Host 'Existing .bot-token.xml found. Overwrite it? (y/N)'
    if ($reuse -notmatch '^[yY]') {
        Write-Host '[4/5] keeping existing token' -ForegroundColor Green
    }
}
if (-not (Test-Path -LiteralPath $tokenFile) -or $reuse -match '^[yY]') {
    $secure = Read-Host 'Discord BOT token (hidden input)' -AsSecureString
    if ($secure.Length -eq 0) { throw 'Token is empty.' }
    $credential = New-Object System.Management.Automation.PSCredential('discord-bot', $secure)
    $credential | Export-Clixml -LiteralPath $tokenFile
    Write-Host '[4/5] token encrypted to .bot-token.xml' -ForegroundColor Green
}

# 5. scheduled task
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSScriptRoot\run-bot.ps1`"" `
    -WorkingDirectory $PSScriptRoot
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "[5/5] scheduled task '$taskName' registered" -ForegroundColor Green

Start-ScheduledTask -TaskName $taskName
Write-Host ''
Write-Host 'Started. Verify with: .\status-bot.ps1' -ForegroundColor Cyan
Write-Host 'Log: logs\discord.log  |  supervisor: logs\supervisor.log' -ForegroundColor Cyan
