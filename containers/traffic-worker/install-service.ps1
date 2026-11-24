$ErrorActionPreference='Stop'
$taskName='KamDispatch Traffic Service'
if(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue){throw 'Task already exists; review before replacing'}
$serviceRoot='D:\traffic-worker\linux-pilot'
$action=New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Argument '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File D:\traffic-worker\linux-pilot\containers\traffic-worker\ensure-service.ps1'
$account=$env:COMPUTERNAME+'\Konstantin'
$principal=New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$triggers=@((New-ScheduledTaskTrigger -AtLogOn -User $account),(New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)))
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Trigger $triggers -Settings $settings -Description 'Local traffic dashboard and durable five-minute scheduler; loopback only.' | Out-Null
Start-ScheduledTask -TaskName $taskName
$wsh=New-Object -ComObject WScript.Shell
$shortcut=$wsh.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Дорожная обстановка.lnk'))
$shortcut.TargetPath='C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe'
$shortcut.Arguments='--user-data-dir="D:\traffic-worker\yandex-profile" http://127.0.0.1:8765'
$shortcut.WorkingDirectory=$serviceRoot
$shortcut.Description='Дорожная обстановка — Камчатка'
$shortcut.Save()
'Installed KamDispatch Traffic Service and desktop shortcut'
