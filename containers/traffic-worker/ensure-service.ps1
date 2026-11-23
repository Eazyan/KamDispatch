$ErrorActionPreference='Stop'
$serviceRoot='D:\traffic-worker\linux-pilot'
$existing=Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object {$_.CommandLine -like '*-m traffic_service.server*' -and $_.CommandLine -like '*D:\traffic-worker\linux-pilot*'}
if(-not $existing){
    Start-Process -FilePath 'D:\traffic-worker\venv\Scripts\pythonw.exe' -ArgumentList @('-m','traffic_service.server','--root',$serviceRoot) -WorkingDirectory $serviceRoot -RedirectStandardOutput "$serviceRoot\data\console.log" -RedirectStandardError "$serviceRoot\data\error.log"
}
