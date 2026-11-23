# Данные пробок для City Sim через SSH

Сбор Яндекс Карт остаётся на Windows-ПК. Команды ниже запускаются на хосте City Sim из каталога `KamDispatch/city-sim` в Bash или совместимой оболочке с OpenSSH и curl.

1. Скопируйте `.env.traffic.example` в `.env.traffic`. Укажите пути к выданному приватному ключу и файлу `known_hosts`. Храните ключ вне репозитория. Публичная часть этого ключа должна быть разрешена для указанного SSH-пользователя.
2. Получите проверенный `known_hosts` от владельца ПК или сверяйте отпечаток ключа сервера перед добавлением. Текущий ключ сервера ED25519 имеет отпечаток `SHA256:KGqooDx9iqmJ7jOHPWHZzeltjuYTIpaNJfpQQfn73tM`.
3. Запустите туннель и оставьте процесс SSH работать.

```sh
. ./.env.traffic
ssh -NT -p "$TRAFFIC_SSH_PORT" -i "$TRAFFIC_SSH_KEY" \
  -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$TRAFFIC_SSH_KNOWN_HOSTS" \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:8765:127.0.0.1:8765 \
  "$TRAFFIC_SSH_USER@$TRAFFIC_SSH_HOST"
```

Туннель слушает только loopback хоста City Sim. Порт 8765 должен быть свободен на этом хосте. Воркер проверяет HTTP-заголовок `Host` и принимает `127.0.0.1:8765` или `localhost:8765`.

В другом терминале на том же хосте проверьте API и скачайте данные.

```sh
curl -fsS http://127.0.0.1:8765/health
curl -fsS http://127.0.0.1:8765/api/graph -o graph.json
curl -fsS http://127.0.0.1:8765/api/latest -o latest.json
```

- `/api/graph` возвращает геометрию коридоров и `graph_sha256`.
- `/api/latest` возвращает интервалы по ID коридоров, `graph_sha256` и время публикации. Перед раскраской дорог сверяйте хеши. Ответ `null` означает, что результатов пока нет. Возраст наблюдений и признак частичного прогона следует передавать в интерфейс.
- Для растрового слоя можно запрашивать пути `frames[].raster` из того же API. Изображения хранятся три часа.

В вебе Eazyan укажите в корневом `.env` параметр `TRAFFIC_WORKER_URL`.
Для Docker Desktop на Mac используйте `http://host.docker.internal:8765`, для
прямого запуска веб-API на хосте — `http://127.0.0.1:8765`. Контейнеру на
другом хосте нужен собственный приватный маршрут до туннеля.

API Eazyan читает только `GET /api/latest` через туннель. Он сверяет SHA-256
результата с локальным `snegoplan/mapprep/out/graph_v3.json` и передаёт браузеру
цветные интервалы вдоль коридоров. Участки без наблюдений остаются без цвета.
Кнопка «Пробки» работает на обзорной и встроенной 3D-карте.
