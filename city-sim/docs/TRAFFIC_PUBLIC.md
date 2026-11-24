# Пробки с Windows-ПК по публичному IP

Windows-воркер собирает данные Яндекс Карт. Отдельный HTTP-порт 8766 выдаёт три
маршрута для чтения. Любой клиент с сетевым доступом к порту может их читать.
HTTP передаёт данные открытым текстом. Управление сбором, панель оператора и
растровый архив доступны на локальном порту воркера 8765.

## Настройка Windows и роутера

1. Скопируйте `traffic_service/public_feed.py` и
   `containers/traffic-worker/install-public-feed.ps1` в те же подкаталоги
   `D:\traffic-worker\linux-pilot` на ПК с работающим воркером.
2. В PowerShell от администратора под учётной записью воркера установите задачу
   и правило брандмауэра. Подставьте LAN IPv4 компьютера.

   ```powershell
   Set-Location D:\traffic-worker\linux-pilot
   .\containers\traffic-worker\install-public-feed.ps1 -BindAddress 192.168.0.3
   curl.exe -fsS http://192.168.0.3:8766/health
   ```

3. Закрепите этот LAN-адрес в DHCP роутера. В Port Forwarding добавьте
   `WAN TCP 8766 → LAN IPv4 ПК:8766`. У роутера должен быть входящий
   публичный IPv4. При CGNAT входящий адрес нужно получить у провайдера.
4. Проверьте доступ из другой сети, например с мобильного интернета.
   Проверка из той же LAN зависит от поддержки NAT loopback в роутере.

## Подключение Docker

Подставьте публичный IPv4 роутера и проверьте сетевой доступ из контейнера.

```sh
export TRAFFIC_WORKER_URL=http://PUBLIC_IPV4:8766
docker run --rm curlimages/curl -fsS --max-time 20 "$TRAFFIC_WORKER_URL/health"
docker run --rm curlimages/curl -fsS --max-time 30 "$TRAFFIC_WORKER_URL/api/graph" > graph.json
docker run --rm curlimages/curl -fsS --max-time 30 "$TRAFFIC_WORKER_URL/api/latest" > latest.json
```

- `/health` показывает доступность воркера. Свежесть смотрите по времени кадров
  и `published_at` в `/api/latest`.
- `/api/graph` отдаёт геометрию. `/api/latest` отдаёт интервалы по ID коридоров
  или `null`, пока публикации нет. Перед раскраской сравните `graph_sha256`.
  Учитывайте частичный статус и срок актуальности 600 секунд.
- Для Eazyan запишите `TRAFFIC_WORKER_URL=http://PUBLIC_IPV4:8766` в корневой
  `.env` и пересоздайте контейнер API командой `docker compose up -d api`.
  Eazyan сверяет хеш публикации со своим `graph_v3.json`.

Чтобы закрыть доступ, удалите проброс на роутере, затем выполните на Windows
от администратора:

```powershell
Stop-ScheduledTask -TaskName 'KamDispatch Public Traffic Feed'
Unregister-ScheduledTask -TaskName 'KamDispatch Public Traffic Feed' -Confirm:$false
Remove-NetFirewallRule -Name 'KamDispatch-Public-Traffic-Feed'
```
