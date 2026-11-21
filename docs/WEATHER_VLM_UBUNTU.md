# Weather VLM на Ubuntu

Устанавливайте сервис на Ubuntu с NVIDIA GPU. Проверьте, что драйвер видит видеокарту и что на диске есть место для модели.

```bash
nvidia-smi
bash cameras/deploy_weather_vlm.sh --diagnose
```

Скрипт устанавливает Python-зависимости и запускает пользовательский сервис systemd, который слушает локальный интерфейс.

```bash
bash cameras/deploy_weather_vlm.sh --install
systemctl --user status weather-vlm.service
journalctl --user -u weather-vlm.service -n 50
curl --fail http://127.0.0.1:8000/healthz
```

Для запуска с уже подготовленными зависимостями и моделью изучите режим `--run-mounted` в [скрипте развёртывания](../cameras/deploy_weather_vlm.sh).

## Подключение к мониторингу

Для удалённого доступа используйте защищённый сетевой маршрут. Если мониторинг обращается через HTTPS-шлюз, настройте на шлюзе проверку доступа и задайте свой URL в `MODEL_URL`. Если шлюз проверяет Bearer-токен, храните его в локальном `.env` как `MODEL_TOKEN`.

Сервис из этого репозитория поддерживает `/v1/scene` и `/v1/snow`. Используйте `MODEL_CONTRACT=scene` для `/v1/scene`. Если ваш сервер поддерживает `/v2/scene`, используйте `MODEL_CONTRACT=scene_v2`.
