$ErrorActionPreference='Stop'
$browserExe='C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe'
$profilePath='D:\traffic-worker\yandex-profile'
$existing=Get-CimInstance Win32_Process -Filter "Name='browser.exe'" | Where-Object {$_.CommandLine -and $_.CommandLine.Contains('--user-data-dir="'+$profilePath+'"') -and $_.CommandLine -notmatch '--type='}
if(-not $existing){
    New-Item -ItemType Directory -Force $profilePath | Out-Null
    Start-Process -FilePath $browserExe -ArgumentList @('--user-data-dir="'+$profilePath+'"','--remote-debugging-address=127.0.0.1','--remote-debugging-port=9223','--no-first-run','--no-default-browser-check','https://yandex.ru/maps/78/petropavlovsk/?ll=158.65%2C53.045&z=15')
}
