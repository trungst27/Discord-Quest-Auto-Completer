$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath $PSScriptRoot
$taskName = 'DiscordBotKeep'

Write-Host '=== DiscordBotKeep status ===' -ForegroundColor Cyan

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host 'Task: NOT REGISTERED — run .\setup-bot.ps1' -ForegroundColor Red
} else {
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    Write-Host "Task:   $($task.State)"
    Write-Host "Last:   $($info.LastRunTime)  result=0x$('{0:X}' -f $info.LastTaskResult)"
    Write-Host "Next:   $($info.NextRunTime)"
}

$proc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*bot.py*' }
if ($proc) {
    Write-Host "Bot:    RUNNING (pid $($proc.ProcessId -join ', '))" -ForegroundColor Green
} else {
    Write-Host 'Bot:    NOT RUNNING' -ForegroundColor Red
}

foreach ($f in @('logs\discord.log', 'logs\supervisor.log')) {
    $p = Join-Path $PSScriptRoot $f
    if (Test-Path -LiteralPath $p) {
        $item = Get-Item -LiteralPath $p
        Write-Host "`n--- $f (last write $($item.LastWriteTime), $([math]::Round($item.Length/1KB,1)) KB) ---" -ForegroundColor DarkGray
        Get-Content -LiteralPath $p -Tail 12
    } else {
        Write-Host "`n--- $f missing ---" -ForegroundColor DarkGray
    }
}
