<#
.SYNOPSIS
  Install / start / stop / check the host-side Agent Mail helper for the product monitor.
.DESCRIPTION
  Default: registers a per-user Scheduled Task "ProductMonitor-AgentMail" (runs at your logon, in your
  own Windows session, no admin rights, hidden pythonw.exe) and starts it now. The helper keeps running
  even when agently-cli is not authorized, so you can log in from the admin page
  (http://127.0.0.1:8080/#notifications -> 「重新授权 / 登录」).
  If Task Scheduler refuses the registration, a shortcut in your Startup folder is used instead.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\Start-AgentMail.ps1             # install auto-start + start now
  powershell -ExecutionPolicy Bypass -File .\scripts\Start-AgentMail.ps1 -Status     # report CLI status to the admin UI, send nothing
  powershell -ExecutionPolicy Bypass -File .\scripts\Start-AgentMail.ps1 -Stop       # stop helper (auto-start stays)
  powershell -ExecutionPolicy Bypass -File .\scripts\Start-AgentMail.ps1 -Uninstall  # stop + remove auto-start
  powershell -ExecutionPolicy Bypass -File .\scripts\Start-AgentMail.ps1 -Login      # log in from this terminal instead of the UI
  powershell -ExecutionPolicy Bypass -File .\scripts\Start-AgentMail.ps1 -Foreground # run in this window for diagnosis
#>
param([switch]$Foreground, [switch]$Status, [switch]$Stop, [switch]$Uninstall, [switch]$Login, [switch]$NoAutoStart)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$TaskName = 'ProductMonitor-AgentMail'
$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot 'agentmail_worker.py'
$log = Join-Path $root 'data\agentmail-worker.log'
$venv = Join-Path $root '.venv\Scripts'
$python = if (Test-Path (Join-Path $venv 'python.exe')) { Join-Path $venv 'python.exe' } else { (Get-Command python -ErrorAction Stop).Source }
$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $python }
$startupLink = Join-Path ([Environment]::GetFolderPath('Startup')) "$TaskName.lnk"
$workerArgs = '"' + $script + '" --log "' + $log + '"'

function Get-Workers {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -like '*agentmail_worker.py*' -and $_.CommandLine -notlike '*--status*' -and $_.CommandLine -notlike '*--once*' }
}
function Stop-Workers {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    $w = @(Get-Workers); $w | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    return $w.Count
}
function Find-Cli {
    $npmRoot = (& npm root -g 2>$null)
    if (-not $npmRoot) { return $null }
    Get-ChildItem -Path (Join-Path $npmRoot '@tencent-qqmail\agently-cli\node_modules\@tencent-qqmail') -Recurse -Filter agently-cli.exe -ErrorAction SilentlyContinue | Select-Object -First 1
}

if ($Stop -or $Uninstall) {
    $n = Stop-Workers
    Write-Output "已停止 $n 个 Agent Mail 助手进程。"
    if ($Uninstall) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Remove-Item $startupLink -ErrorAction SilentlyContinue
        Write-Output '已移除登录自启动。'
    }
    exit 0
}

$cli = Find-Cli
if (-not $cli) { Write-Output '提示：未找到 agently-cli。请先运行 npm install -g @tencent-qqmail/agently-cli（助手仍会启动并在后台显示此状态）。' }
if ($Status) { & $python $script --status; exit $LASTEXITCODE }

if ($Login) {
    if (-not $cli) { exit 2 }
    # Same environment cleanup as the helper, so this login is the one the helper uses.
    Get-ChildItem env: | Where-Object { $_.Name -match '^(CURSOR|CODEX|CLAUDE_CODE|CLAUDECODE|WORKBUDDY)' } | ForEach-Object { Remove-Item "env:$($_.Name)" }
    Write-Output '请点击或复制 agently-cli 输出的链接在浏览器中完成授权：'
    & $cli.FullName auth login
    & $cli.FullName '+me'
    exit $LASTEXITCODE
}

if ($Foreground) { Stop-Workers | Out-Null; & $python $script; exit $LASTEXITCODE }

Stop-Workers | Out-Null
$mode = ''
if (-not $NoAutoStart) {
    try {
        $user = "$env:USERDOMAIN\$env:USERNAME"
        $action = New-ScheduledTaskAction -Execute $pythonw -Argument $workerArgs -WorkingDirectory $root
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
            -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
            -Description '商品监控 Agent Mail 本机助手（发送通知邮件、从管理后台发起 agently-cli 授权）' -Force | Out-Null
        Remove-Item $startupLink -ErrorAction SilentlyContinue
        Start-ScheduledTask -TaskName $TaskName
        $mode = "计划任务 $TaskName（登录时自动启动）"
    } catch {
        Write-Output "计划任务注册失败（$($_.Exception.Message.Trim())），改用「启动」文件夹快捷方式。"
    }
}
if (-not $mode) {
    if (-not $NoAutoStart) {
        $shell = New-Object -ComObject WScript.Shell
        $lnk = $shell.CreateShortcut($startupLink)
        $lnk.TargetPath = $pythonw; $lnk.Arguments = $workerArgs; $lnk.WorkingDirectory = $root; $lnk.WindowStyle = 7
        $lnk.Description = '商品监控 Agent Mail 本机助手'; $lnk.Save()
        $mode = "「启动」文件夹快捷方式（登录时自动启动）"
    } else { $mode = '本次手动启动（未设置自启动）' }
    Start-Process -FilePath $pythonw -ArgumentList $workerArgs -WorkingDirectory $root -WindowStyle Hidden | Out-Null
}
Start-Sleep -Seconds 4
$running = @(Get-Workers)
Write-Output "Agent Mail 助手：$(if ($running.Count) { "运行中 PID=$($running[0].ProcessId)" } else { '未检测到进程，请查看 ' + $log })；自启动方式：$mode。"
Write-Output '打开 http://127.0.0.1:8080/#notifications ，未授权时点击「重新授权 / 登录」。'
