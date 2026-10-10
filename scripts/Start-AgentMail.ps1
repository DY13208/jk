param([switch]$Foreground)
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = (Get-Command python -ErrorAction Stop).Source
$taskScript = Join-Path $PSScriptRoot 'agentmail_worker.py'
if ($Foreground) {
    & $taskPython $taskScript
} else {
    $taskLogDir = Join-Path $taskRoot 'data'
    $taskWorker = Start-Process -FilePath $taskPython -ArgumentList @('"' + $taskScript + '"') -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLogDir 'agentmail-worker.log') -RedirectStandardError (Join-Path $taskLogDir 'agentmail-worker-error.log')
    Write-Output "Agent Mail 工作进程已启动，PID=$($taskWorker.Id)。请在后台通知设置查看状态。"
}
