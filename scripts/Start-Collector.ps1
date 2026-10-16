param([switch]$Foreground,[switch]$Stop,[switch]$Status,[switch]$NoAutoStart)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$python=Join-Path $root '.crawler-venv\Scripts\python.exe'
$pythonw=Join-Path $root '.crawler-venv\Scripts\pythonw.exe'
$worker=Join-Path $PSScriptRoot 'collector_worker.py'
$taskName='ProductMonitor-Collector'
if ($Status) {
    Invoke-RestMethod 'http://127.0.0.1:8080/api/collector/status'
    exit
}
if ($Stop) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue }
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($worker,[StringComparison]::OrdinalIgnoreCase) -ge 0 } | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
    exit
}
if (!(Test-Path -LiteralPath $python)) { throw '请先创建 .crawler-venv 并安装 requirements-collector.txt' }
if ($Foreground) { & $python $worker; exit }
if (!$NoAutoStart) {
    try {
        $action=New-ScheduledTaskAction -Execute $pythonw -Argument ('"'+$worker+'"') -WorkingDirectory $root
        $trigger=New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
        $settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
        Write-Output '本机采集已启动，并配置登录后自动启动。'
        exit
    } catch { Write-Warning '无法注册计划任务，改为当前会话启动。' }
}
Start-Process -FilePath $pythonw -ArgumentList ('"'+$worker+'"') -WorkingDirectory $root -WindowStyle Hidden
Write-Output '本机采集进程已启动。'
