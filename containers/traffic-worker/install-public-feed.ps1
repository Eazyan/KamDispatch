param(
    [Parameter(Mandatory=$true)][string]$BindAddress,
    [string]$WorkerRoot='D:\traffic-worker\linux-pilot',
    [string]$Python='D:\traffic-worker\venv\Scripts\pythonw.exe'
)

$ErrorActionPreference='Stop'
$taskName='KamDispatch Public Traffic Feed'
$program=Join-Path $WorkerRoot 'traffic_service\public_feed.py'
if (-not (Test-Path -LiteralPath $program) -or -not (Test-Path -LiteralPath $Python)) {
    throw 'The public feed script and the worker Python environment must exist.'
}
$consolePython=Join-Path (Split-Path -Parent $Python) 'python.exe'
$basePython=& $consolePython -c 'import sys; print(sys._base_executable)'
if ($LASTEXITCODE -ne 0 -or -not $basePython) {
    throw 'Could not resolve the Python executable for the firewall rule.'
}
$listenerProgram=Join-Path (Split-Path -Parent $basePython) (Split-Path -Leaf $Python)
if (-not (Test-Path -LiteralPath $listenerProgram)) {
    throw 'Resolved firewall program does not exist.'
}
Get-NetIPAddress -AddressFamily IPv4 -IPAddress $BindAddress | Out-Null
$account=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$action=New-ScheduledTaskAction -Execute $Python -Argument ('"{0}" --bind {1}' -f $program,$BindAddress)
$principal=New-ScheduledTaskPrincipal -UserId $account -LogonType S4U -RunLevel Limited
$triggers=@((New-ScheduledTaskTrigger -AtStartup),(New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)))
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
if ((Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue).State -eq 'Running') {
    Stop-ScheduledTask -TaskName $taskName
}
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Trigger $triggers -Settings $settings -Force | Out-Null
Get-NetFirewallRule -Name 'KamDispatch-Public-Traffic-Feed' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name 'KamDispatch-Public-Traffic-Feed' -DisplayName $taskName -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $BindAddress -LocalPort 8766 -Program $listenerProgram | Out-Null
Start-ScheduledTask -TaskName $taskName
