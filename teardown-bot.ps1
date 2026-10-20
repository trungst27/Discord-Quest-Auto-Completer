$ErrorActionPreference = 'Continue'
$taskName = 'DiscordBotKeep'

Write-Host '=== Stopping DiscordBotKeep ===' -ForegroundColor Cyan

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Task '$taskName' unregistered." -ForegroundColor Green
} else {
    Write-Host 'Task was not registered.'
}

# run-bot.ps1 holds the mutex; kill the supervisor + bot by command line.
$targets = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*Bot*Disscord*bot.py*' }
foreach ($p in $targets) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Killed python pid $($p.ProcessId)" -ForegroundColor Green
}

$sup = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*run-bot.ps1*' }
foreach ($p in $sup) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Killed supervisor pid $($p.ProcessId)" -ForegroundColor Green
}

Write-Host 'Stopped.' -ForegroundColor Cyan
